"""Retain owned repository runs from snapshot tags, independently of report history."""

import json
from datetime import datetime, timezone
from time import time
from uuid import UUID

from drastic_agent.agent.enums import AgentOperationSource, AgentOperationState, AgentOperationType
from drastic_agent.agent.report import AgentReport
from drastic_common.restic.exceptions import is_repository_locked
from drastic_common.restic.snapshots import snapshot_uuid


def parse_datetime(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if value:
        try:
            return datetime.fromisoformat(value).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def keep_operation_ids(operations, retention):
    """Select whole runs from newest-first timestamps using UTC calendar buckets."""
    kept = set()
    keep_last = int(retention.get("keep_last") or 0)
    if keep_last > 0:
        kept.update(operation["id"] for operation in operations[:keep_last])
    for field, bucket in (("keep_hourly", "hourly"), ("keep_weekly", "weekly"),
                          ("keep_monthly", "monthly"), ("keep_yearly", "yearly")):
        limit = int(retention.get(field) or 0)
        if limit <= 0:
            continue
        seen = set()
        for operation in operations:
            started = parse_datetime(operation["started"])
            if started == datetime.min.replace(tzinfo=timezone.utc):
                continue
            key = (started.isocalendar()[:2] if bucket == "weekly" else
                   started.strftime({"hourly": "%Y-%m-%d-%H", "monthly": "%Y-%m", "yearly": "%Y"}[bucket]))
            if key not in seen:
                seen.add(key)
                kept.add(operation["id"])
                if len(seen) >= limit:
                    break
    return kept


def validate_policy(policy):
    if policy is None:
        return
    fields = ("keep_last", "keep_hourly", "keep_weekly", "keep_monthly", "keep_yearly")
    if not isinstance(policy, dict) or set(policy) - {*fields, "name"}:
        raise ValueError("Invalid retention policy")
    values = [policy.get(key) if policy.get(key) is not None else 0 for key in fields]
    if any(type(value) is not int or value < 0 for value in values) or not any(values):
        raise ValueError("Retention must keep at least one backup")


def validate_context(context):
    if (not isinstance(context, dict) or set(context) != {"owner_uuid", "job_uuids", "default", "running_operation_uuids", "check_token"}
            or not isinstance(context["job_uuids"], list) or not isinstance(context["running_operation_uuids"], list)):
        raise ValueError("Missing or incomplete backend retention context; cleanup deferred")
    context["owner_uuid"] = str(UUID(context["owner_uuid"]))
    validate_policy(context["default"])
    return context


class RetentionService:
    @staticmethod
    def pending_tasks(settings):
        tasks = []
        for row in settings.all():
            if row["name"].startswith("retention_task:"):
                payload = json.loads(row["settings"])
                if payload["retry_at"] <= time():
                    tasks.append(payload["args"])
        return tasks

    @staticmethod
    def record_check_failure(settings, repository_id):
        settings.upsert({"name": f"retention_check_failed:{repository_id}", "settings": "100%"}, ["name"])

    @staticmethod
    def select_runs(snapshots, context, policies, current_operation_uuid):
        groups = {}
        running = set(context["running_operation_uuids"]) - {current_operation_uuid}
        for snapshot in snapshots.values():
            if snapshot_uuid(snapshot, "drastic-owner") != context["owner_uuid"]:
                continue
            job, operation = snapshot_uuid(snapshot, "job_uuid"), snapshot_uuid(snapshot, "operation_uuid")
            if not job or not operation or operation in running or policies.get(job) is None:
                continue
            started = parse_datetime(snapshot.get("time"))
            if started == datetime.min.replace(tzinfo=timezone.utc):
                raise ValueError("Owned snapshot has an invalid timestamp; cleanup stopped")
            run = groups.setdefault(job, {}).setdefault(operation, {"id": operation, "started": started, "snapshot_ids": []})
            run["started"] = min(run["started"], started)
            run["snapshot_ids"].append(snapshot["id"])
        selected = []
        for job_uuid, job_runs in groups.items():
            runs = sorted(job_runs.values(), key=lambda run: (run["started"], run["id"]), reverse=True)
            kept = keep_operation_ids(runs, policies[job_uuid])
            kept.add(current_operation_uuid)
            for run in runs:
                if run["id"] not in kept:
                    selected.extend(run["snapshot_ids"])
        return selected

    @classmethod
    def run(cls, *, repository_id, job_id, job_uuid, current_operation_uuid, current_policy,
            request_context, set_repository, resticapi, settings, send_report):
        task_key = f"retention_task:{repository_id}:{job_uuid or 'repository'}"
        prune_key = f"retention_prune:{repository_id}"
        settings.upsert({"name": task_key, "settings": json.dumps({"args": {
            "repository_id": repository_id, "job_id": job_id, "job_uuid": job_uuid,
            "current_operation_uuid": current_operation_uuid}, "retry_at": time() + 900})}, ["name"])
        report = AgentReport(type=AgentOperationType.retention, repository_id=repository_id, job_id=job_id,
            parent_operation_uuid=current_operation_uuid,
            source=AgentOperationSource.triggered if current_operation_uuid else AgentOperationSource.manual,
            data={"scope": "repository", "job_uuid": job_uuid, "cleanup_pending": True})
        try:
            validate_policy(current_policy)
            set_repository(repository_id)
            snapshots = {item["id"]: item for item in resticapi.snapshots() or []}
            context = validate_context(request_context())
            owner = context["owner_uuid"]
            scanned_jobs = {snapshot_uuid(snapshot, "job_uuid") for snapshot in snapshots.values()
                            if snapshot_uuid(snapshot, "drastic-owner") == owner} - {None}
            policies = dict.fromkeys(scanned_jobs - set(context["job_uuids"]), context["default"])
            if job_uuid in context["job_uuids"]:
                policies[job_uuid] = current_policy
            selected = cls.select_runs(snapshots, context, policies, current_operation_uuid)
            if selected or settings.find_one(name=prune_key):
                full = bool(context["check_token"] or settings.find_one(name=f"retention_check_failed:{repository_id}"))
                try:
                    report.data["check"] = resticapi.check(read_data=full, read_data_subset=None)
                except Exception as exc:
                    if is_repository_locked(exc):
                        report.data["repository_locked"] = True
                    else:
                        cls.record_check_failure(settings, repository_id)
                        report.data["retention_check_failed"] = True
                    raise
                report.data.update(retention_check_full=full, retention_check_verified=context["check_token"])
                snapshots = {item["id"]: item for item in resticapi.snapshots() or []}
                # Acknowledge full verification, not a deletion plan, and check for newer failures.
                if full and not send_report(report):
                    raise ValueError("Repository verification could not be acknowledged; cleanup deferred")
                context = validate_context(request_context())
                if context["owner_uuid"] != owner or context["check_token"]:
                    raise ValueError("A repository check failure remains unresolved; cleanup deferred")
                settings.delete(name=f"retention_check_failed:{repository_id}")
                selected = cls.select_runs(snapshots, context, policies, current_operation_uuid)
                settings.upsert({"name": prune_key, "settings": "pending"}, ["name"])
                for start in range(0, len(selected), 1000):
                    resticapi.forget_snapshots(selected[start:start + 1000], prune=False)
                report.data["prune"] = resticapi.prune()
                report.data["deleted_snapshot_ids"] = selected
                settings.delete(name=prune_key)
            settings.delete(name=task_key)
            report.data["cleanup_pending"] = False
        except Exception as exc:
            report.log_message(str(exc), final_state=AgentOperationState.warning)
        return report.finish()
