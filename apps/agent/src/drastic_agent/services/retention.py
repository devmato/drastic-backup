"""Retain whole reported backup runs, using restic tags to verify their snapshots."""

import json
from datetime import datetime, timezone
from time import time

from drastic_agent.agent.enums import AgentOperationSource, AgentOperationState, AgentOperationType
from drastic_agent.agent.report import AgentReport
from drastic_common.restic.exceptions import is_repository_locked


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
    """Select whole runs from newest-first reports using UTC calendar buckets."""
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
            ended = parse_datetime(operation.get("ended") or operation.get("started"))
            if ended == datetime.min.replace(tzinfo=timezone.utc):
                continue
            key = (ended.isocalendar()[:2] if bucket == "weekly" else
                   ended.strftime({"hourly": "%Y-%m-%d-%H", "monthly": "%Y-%m", "yearly": "%Y"}[bucket]))
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
    if (not isinstance(context, dict) or set(context) != {"job_uuids", "default", "runs", "check_token"}
            or not isinstance(context["job_uuids"], list) or not isinstance(context["runs"], list)):
        raise ValueError("Missing or incomplete backend retention context; cleanup deferred")
    validate_policy(context["default"])
    return context


class RetentionService:
    @staticmethod
    def pending_tasks(settings):
        tasks = []
        for row in settings.all():
            if row["name"].startswith("retention_task:"):
                payload = json.loads(row["settings"])
                if payload.get("retry_at", 0) <= time():
                    payload["args"].pop("retention_id", None)
                    tasks.append(payload["args"])
        return tasks

    @staticmethod
    def record_check_failure(settings, repository_id):
        settings.upsert({"name": f"retention_check_failed:{repository_id}", "settings": "100%"}, ["name"])

    @staticmethod
    def select_runs(runs, snapshots, policies, current_operation_uuid):
        groups = {}
        for run in runs:
            groups.setdefault(run["job_uuid"], []).append(run)
        selected = []
        for job_uuid, job_runs in groups.items():
            policy = policies.get(job_uuid)
            if policy is None:
                continue
            # Select from the reports first. Missing snapshots only block cleanup when
            # their run must be kept; expired runs may already be partly removed.
            eligible = [run for run in job_runs if (run["finished"] or run["id"] == current_operation_uuid)
                        and any(a["snapshot_id"] and not a["forgotten"] for a in run["artifacts"])]
            if not eligible:
                continue
            eligible.sort(key=lambda run: (parse_datetime(run["ended"]), run["id"]), reverse=True)
            kept = keep_operation_ids(eligible, policy)
            if not kept:
                raise ValueError("No backup could be selected for retention; cleanup stopped")
            kept.add(current_operation_uuid)
            for run in eligible:
                artifacts = [a for a in run["artifacts"] if a["snapshot_id"] and not a["forgotten"]]
                for artifact in artifacts:
                    snapshot = snapshots.get(artifact["snapshot_id"])
                    if snapshot is None:
                        if run["id"] in kept:
                            raise ValueError("A retained backup has missing snapshots; cleanup stopped")
                        continue
                    expected = {f"job_uuid:{job_uuid}", f"operation_uuid:{run['id']}",
                                f"artifact_uuid:{artifact['uuid']}", f"artifact_key:{artifact['artifact_key']}"}
                    if not expected.issubset(snapshot.get("tags") or []):
                        raise ValueError("Snapshot does not match its backup report; cleanup stopped")
                if run["id"] not in kept:
                    selected.extend({"uuid": a["uuid"], "snapshot_id": a["snapshot_id"], "operation_uuid": run["id"]}
                                    for a in artifacts)
        return selected

    @classmethod
    def run(cls, *, repository_id, job_id, job_uuid, current_operation_uuid, current_policy,
            request_context, set_repository, resticapi, settings, source=None):
        task_key = f"retention_task:{repository_id}:{job_uuid or job_id or 'repository'}"
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
            scanned_jobs = {tag.removeprefix("job_uuid:") for snapshot in snapshots.values()
                            for tag in snapshot.get("tags", []) if tag.startswith("job_uuid:")}
            # This also acknowledges the current backup result before it can replace older runs.
            context = validate_context(request_context(source))
            policies = dict.fromkeys(scanned_jobs - set(context["job_uuids"]), context["default"])
            if job_uuid in context["job_uuids"]:
                policies[job_uuid] = current_policy
            selected = cls.select_runs(context["runs"], snapshots, policies, current_operation_uuid)
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
                context = validate_context(request_context(report if full else None))
                if context["check_token"]:
                    raise ValueError("A repository check failure remains unresolved; cleanup deferred")
                settings.delete(name=f"retention_check_failed:{repository_id}")
                selected = cls.select_runs(context["runs"], snapshots, policies, current_operation_uuid)
                ids = [item["snapshot_id"] for item in selected if item["snapshot_id"] in snapshots]
                settings.upsert({"name": prune_key, "settings": "pending"}, ["name"])
                for start in range(0, len(ids), 1000):
                    resticapi.forget_snapshots(ids[start:start + 1000], prune=False)
                report.data["prune"] = resticapi.prune()
                # Until prune succeeds, reports retain the original IDs. Re-evaluating
                # them also retries prune when forget already removed every selected snapshot.
                report.data["forgotten_artifacts"] = [{**item, "forgotten_at": datetime.now(timezone.utc).isoformat()}
                                                       for item in selected]
                settings.delete(name=prune_key)
            settings.delete(name=task_key)
            report.data["cleanup_pending"] = False
        except Exception as exc:
            report.log_message(str(exc), final_state=AgentOperationState.warning)
        return report.finish()
