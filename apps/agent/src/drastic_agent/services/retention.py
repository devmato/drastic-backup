import json
from datetime import datetime, timezone
from time import time

from drastic_agent.agent.enums import AgentOperationSource, AgentOperationState, AgentOperationType
from drastic_agent.agent.report import AgentReport


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        # Legacy naive timestamps were written in the agent's local timezone.
        return datetime.fromisoformat(str(value)).astimezone(timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _retention_bucket(run, bucket):
    ended = _parse_datetime(run.get("ended") or run.get("started"))
    if ended == datetime.min.replace(tzinfo=timezone.utc):
        return None
    ended = ended.astimezone()
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
    @staticmethod
    def pending_tasks(settings_table):
        tasks = []
        for row in settings_table.all():
            if not str(row.get("name", "")).startswith("retention_task:"):
                continue
            payload = json.loads(row["settings"])
            if payload.get("retry_at", 0) <= time():
                tasks.append(payload["args"])
        return tasks

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
        settings_table,
        retry=False,
    ):
        task_key = f"retention_task:{repository_id}:{job_id}"
        prune_key = f"retention_prune:{repository_id}"
        prune_only = retry and retention_id is None
        task = {"args": {"repository_id": repository_id, "retention_id": retention_id,
                         "job_id": job_id, "current_operation_uuid": current_operation_uuid},
                "retry_at": time() + 900}
        # Keep intent before any deletion, including across agent restarts.
        settings_table.upsert({"name": task_key, "settings": json.dumps(task)}, ["name"])
        report = AgentReport(
            type=AgentOperationType.retention,
            repository_id=repository_id,
            job_id=job_id,
            retention_id=retention_id,
            parent_operation_uuid=current_operation_uuid,
            source=AgentOperationSource.triggered if current_operation_uuid else AgentOperationSource.manual,
        )
        try:
            retention = None if prune_only else retentions_table.find_one(id=retention_id)
        except Exception as exc:
            report.log_message(
                f"Could not load retention policy {retention_id}: {exc}",
                final_state=AgentOperationState.failed,
            )
            return report.finish()

        if not retention and not prune_only:
            prune_pending = bool(settings_table.find_one(name=prune_key))
            if prune_pending:
                # Forget may already have succeeded. Keep only repository cleanup,
                # never select more snapshots using the deleted policy.
                task["args"]["retention_id"] = None
                settings_table.upsert({"name": task_key, "settings": json.dumps(task)}, ["name"])
            else:
                settings_table.delete(name=task_key)
            report.retention_id = None  # The removed policy cannot be referenced by the backend.
            report.data = {"retired_policy_id": retention_id, "cleanup_pending": prune_pending}
            report.log_message(
                f"Retention policy with id {retention_id} no longer available; policy retry retired"
                + ("; pending prune retained" if prune_pending else ""),
                final_state=AgentOperationState.warning,
            )
            return report.finish()

        try:
            if prune_only and not settings_table.find_one(name=prune_key):
                settings_table.delete(name=task_key)
                report.log_message("Pending prune already completed")
                return report.finish()
            repository = set_repository(repository_id)
            blocked_check = settings_table.find_one(name=f"retention_check_failed:{repository_id}")
            if retry or blocked_check:
                report.log_message("Retrying pending retention; checking repository before deletion")
                if blocked_check and blocked_check.get("settings"):
                    resticapi.check(read_data=True, read_data_subset=None)
                else:
                    resticapi.check()
                settings_table.delete(name=f"retention_check_failed:{repository_id}")
            if prune_only:
                report.log_message("Pruning repository after policy removal; no snapshots will be forgotten")
                report.data["prune"] = resticapi.prune()
                settings_table.delete(name=prune_key)
                settings_table.delete(name=task_key)
                return report.finish()
            report.log_message(
                f"Running retention policy: {retention['name']} on repository {repository['location']}"
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
            current = next((operation for operation in backup_operations
                            if operation.get("uuid") == current_operation_uuid
                            and operation.get("state") == AgentOperationState.running.name), None)
            if current and not (current.get("data") or {}).get("partial_failure"):
                artifacts = list(operation_artifacts_table.find(operation_id=current["id"]))
                if artifacts and all(artifact.get("state") == AgentOperationState.success.name
                                     and artifact.get("snapshot_id") for artifact in artifacts):
                    # The snapshot is complete, but the parent finishes only after retention/hooks.
                    successful_operations.append({**current, "ended": datetime.now(timezone.utc).isoformat()})
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

            # Repository admission serializes retention on this agent. Keep the intent
            # outside synced repository data, and commit it before forget can succeed.
            if snapshot_ids or missing_artifacts:
                settings_table.upsert({"name": prune_key, "settings": "pending"}, ["name"])

            if snapshot_ids:
                report.data["forget"] = resticapi.forget_snapshots(snapshot_ids, prune=False)

            forgotten_at = datetime.now(timezone.utc).isoformat()
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

            if settings_table.find_one(name=prune_key):
                report.log_message("Pruning repository after snapshot removal")
                report.data["prune"] = resticapi.prune()
                settings_table.delete(name=prune_key)

            if unknown_artifacts:
                report.log_message(
                    "Skipped snapshots whose repository tags do not match local artifacts: "
                    + ", ".join(artifact["snapshot_id"] for artifact in unknown_artifacts),
                    final_state=AgentOperationState.warning,
                )

            report.log_message(f"Removed {len(snapshot_ids)} snapshots: {', '.join(snapshot_ids)}")
        except Exception as exc:
            report.log_message(f"Error during retention: {exc}", final_state=AgentOperationState.failed)

        if report.final_state == AgentOperationState.success:
            settings_table.delete(name=task_key)
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
