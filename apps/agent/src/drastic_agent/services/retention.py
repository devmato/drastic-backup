from datetime import datetime

from drastic_agent.agent.enums import AgentOperationSource, AgentOperationState, AgentOperationType
from drastic_agent.agent.report import AgentReport


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min


def _retention_bucket(run, bucket):
    ended = _parse_datetime(run.get("ended") or run.get("started"))
    if ended == datetime.min:
        return None
    if bucket == "hourly":
        return ended.strftime("%Y-%m-%d-%H")
    if bucket == "weekly":
        year, week, _ = ended.isocalendar()
        return f"{year}-W{week:02d}"
    if bucket == "monthly":
        return ended.strftime("%Y-%m")
    if bucket == "yearly":
        return ended.strftime("%Y")
    return None


class RetentionService:
    @classmethod
    def run(
        cls,
        *,
        repository_id,
        retention_id,
        job_id,
        current_operation_uuid=None,
        set_repository,
        resticapi,
        retentions_table,
        operations_table,
        operation_artifacts_table,
    ):
        report = AgentReport(
            type=AgentOperationType.retention,
            repository_id=repository_id,
            job_id=job_id,
            retention_id=retention_id,
            parent_operation_uuid=current_operation_uuid,
            source=AgentOperationSource.triggered if current_operation_uuid else AgentOperationSource.manual,
        )
        try:
            retention = retentions_table.find_one(id=retention_id)
        except Exception as exc:
            report.log_message(
                f"Could not load retention policy {retention_id}: {exc}",
                final_state=AgentOperationState.failed,
            )
            return report.finish()

        if not retention:
            report.log_message(
                f"Retention policy with id {retention_id} not found",
                final_state=AgentOperationState.failed,
            )
            return report.finish()

        try:
            repository = set_repository(repository_id)
            report.log_message(
                f"Running retention policy: {retention['name']} on repository {repository['location']} with prune"
            )
            backup_operations = list(
                operations_table.find(
                    job_id=job_id, repository_id=repository_id, type="backup"
                )
            )
            successful_operations = [
                operation
                for operation in backup_operations
                if operation.get("state") in {AgentOperationState.success.name, AgentOperationState.warning.name}
            ]
            partial_operations = [
                operation
                for operation in backup_operations
                if operation.get("state") == AgentOperationState.failed.name
                and isinstance(operation.get("data"), dict)
                and operation["data"].get("partial_failure") is True
            ]
            successful_operations.sort(
                key=lambda operation: _parse_datetime(
                    operation.get("ended") or operation.get("started")
                ),
                reverse=True,
            )

            keep_operation_ids = cls.keep_operation_ids(successful_operations, retention)
            candidate_operations = [
                operation
                for operation in [*successful_operations, *partial_operations]
                if operation.get("id") not in keep_operation_ids and operation.get("uuid") != current_operation_uuid
            ]
            candidate_operation_ids = {operation["id"] for operation in candidate_operations}
            candidate_artifacts = []
            for operation in candidate_operations:
                candidate_artifacts.extend(
                    (operation, artifact)
                    for artifact in operation_artifacts_table.find(operation_id=operation["id"])
                    if artifact.get("snapshot_id") and not artifact.get("forgotten_at")
                    and (
                        operation.get("state") != AgentOperationState.failed.name
                        or artifact.get("state") == AgentOperationState.success.name
                    )
                )

            repository_snapshots = resticapi.snapshots() or []
            if isinstance(repository_snapshots, dict):
                repository_snapshots = [repository_snapshots]
            snapshots_by_id = {
                snapshot.get("id"): snapshot
                for snapshot in repository_snapshots
                if snapshot.get("id")
            }

            reconciled_artifacts = []
            missing_artifacts = []
            unknown_artifacts = []
            for operation, artifact in candidate_artifacts:
                snapshot = snapshots_by_id.get(artifact["snapshot_id"])
                if snapshot is None:
                    missing_artifacts.append(artifact)
                    continue

                tags = set(snapshot.get("tags") or [])
                expected_tags = {
                    f"operation_uuid:{operation['uuid']}",
                    f"artifact_uuid:{artifact['uuid']}",
                }
                if not expected_tags.issubset(tags):
                    unknown_artifacts.append(artifact)
                    continue
                reconciled_artifacts.append(artifact)

            snapshot_ids = [artifact["snapshot_id"] for artifact in reconciled_artifacts]

            report.data = {
                "kept_operation_ids": sorted(keep_operation_ids),
                "pruned_operation_ids": sorted(candidate_operation_ids),
                "snapshot_ids": snapshot_ids,
                "forgotten_artifacts": [],
                "missing_snapshot_ids": [
                    artifact["snapshot_id"] for artifact in missing_artifacts
                ],
                "skipped_snapshot_ids": [
                    artifact["snapshot_id"] for artifact in unknown_artifacts
                ],
            }

            if snapshot_ids:
                report.data["forget"] = resticapi.forget_snapshots(snapshot_ids, prune=False)

            forgotten_at = datetime.now().isoformat()
            for artifact in [*reconciled_artifacts, *missing_artifacts]:
                artifact["forgotten_at"] = forgotten_at
                operation_artifacts_table.update(artifact, ["id"])
                report.data["forgotten_artifacts"].append(
                    {
                        "uuid": artifact.get("uuid"),
                        "forgotten_at": forgotten_at,
                        "snapshot_id": artifact.get("snapshot_id"),
                        "repository_missing": artifact in missing_artifacts,
                    }
                )

            if candidate_operation_ids:
                report.data["prune"] = resticapi.prune()

            if unknown_artifacts:
                report.log_message(
                    "Skipped snapshots whose repository tags do not match local artifacts: "
                    + ", ".join(artifact["snapshot_id"] for artifact in unknown_artifacts),
                    final_state=AgentOperationState.warning,
                )

            report.log_message(f"Removed {len(snapshot_ids)} snapshots: {', '.join(snapshot_ids)}")
        except Exception as exc:
            report.log_message(f"Error during retention: {exc}", final_state=AgentOperationState.failed)

        return report.finish()

    @staticmethod
    def keep_operation_ids(operations, retention):
        keep_operation_ids = set()

        keep_last = int(retention.get("keep_last") or 0)
        if keep_last > 0:
            keep_operation_ids.update(operation["id"] for operation in operations[:keep_last])

        for field_name, bucket_name in (
            ("keep_hourly", "hourly"),
            ("keep_weekly", "weekly"),
            ("keep_monthly", "monthly"),
            ("keep_yearly", "yearly"),
        ):
            limit = int(retention.get(field_name) or 0)
            if limit <= 0:
                continue

            seen_buckets = set()
            for operation in operations:
                bucket = _retention_bucket(operation, bucket_name)
                if not bucket or bucket in seen_buckets:
                    continue
                keep_operation_ids.add(operation["id"])
                seen_buckets.add(bucket)
                if len(seen_buckets) >= limit:
                    break

        return keep_operation_ids

    keep_run_ids = keep_operation_ids
