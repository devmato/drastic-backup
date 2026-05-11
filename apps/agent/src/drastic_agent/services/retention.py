from datetime import datetime

from drastic_agent.agent.enums import AgentOperationSource, AgentOperationState, AgentOperationType
from drastic_agent.agent.report import AgentReport
from drastic_common.restic.exceptions import ResticError


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
        repository = set_repository(repository_id)
        retention = retentions_table.find_one(id=retention_id)
        report = AgentReport(
            type=AgentOperationType.retention,
            repository_id=repository_id,
            job_id=job_id,
            retention_id=retention_id,
            parent_operation_uuid=current_operation_uuid,
            source=AgentOperationSource.triggered if current_operation_uuid else AgentOperationSource.manual,
        )

        if not retention:
            report.log_message(
                f"Retention policy with id {retention_id} not found",
                final_state=AgentOperationState.failed,
            )
            return report.finish()

        try:
            report.log_message(
                f"Running retention policy: {retention['name']} on repository {repository['location']} with prune"
            )
            operations = [
                operation
                for operation in operations_table.find(job_id=job_id, repository_id=repository_id, type="backup")
                if operation.get("state") in {AgentOperationState.success.name, AgentOperationState.warning.name}
            ]
            operations.sort(key=lambda operation: _parse_datetime(operation.get("ended") or operation.get("started")), reverse=True)

            keep_operation_ids = cls.keep_operation_ids(operations, retention)
            candidate_operations = [
                operation
                for operation in operations
                if operation.get("id") not in keep_operation_ids and operation.get("uuid") != current_operation_uuid
            ]
            candidate_operation_ids = {operation["id"] for operation in candidate_operations}
            candidate_artifacts = []
            for operation_id in candidate_operation_ids:
                candidate_artifacts.extend(
                    artifact
                    for artifact in operation_artifacts_table.find(operation_id=operation_id)
                    if artifact.get("snapshot_id") and not artifact.get("forgotten_at")
                )
            snapshot_ids = [artifact["snapshot_id"] for artifact in candidate_artifacts]

            report.data = {
                "kept_operation_ids": sorted(keep_operation_ids),
                "pruned_operation_ids": sorted(candidate_operation_ids),
                "snapshot_ids": snapshot_ids,
                "forgotten_artifacts": [],
            }

            if snapshot_ids:
                report.data["forget"] = resticapi.forget_snapshots(snapshot_ids, prune=True)
                forgotten_at = datetime.now().isoformat()
                for artifact in candidate_artifacts:
                    artifact["forgotten_at"] = forgotten_at
                    operation_artifacts_table.update(artifact, ["id"])
                    report.data["forgotten_artifacts"].append(
                        {
                            "uuid": artifact.get("uuid"),
                            "forgotten_at": forgotten_at,
                            "snapshot_id": artifact.get("snapshot_id"),
                        }
                    )

            report.log_message(f"Removed {len(snapshot_ids)} snapshots: {', '.join(snapshot_ids)}")
        except ResticError as exc:
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
