"""Retain owned repository runs from snapshot tags, independently of report history."""

import json
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from drastic_agent.agent.enums import AgentOperationSource, AgentOperationState, AgentOperationType
from drastic_agent.agent.report import AgentReport
from drastic_common.restic.exceptions import is_repository_locked
from drastic_common.restic.snapshots import run_is_complete, snapshot_uuid
from drastic_common.retention import RetentionPolicySchema
from drastic_common.scheduling import validate_timezone


def parse_datetime(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    return datetime.min.replace(tzinfo=timezone.utc)


def period_key(started, period):
    if period == "hour":
        # Repeated wall-clock hours at the autumn DST transition are distinct hours.
        return started.date(), started.hour, started.utcoffset()
    if period == "day":
        return started.date()
    if period == "week":
        return started.isocalendar()[:2]
    if period == "month":
        return started.year, started.month
    return started.year


def calendar_age(started, now, period):
    if period == "day":
        return (now.date() - started.date()).days
    if period == "week":
        return ((now.date() - timedelta(days=now.weekday())) -
                (started.date() - timedelta(days=started.weekday()))).days // 7
    if period == "month":
        return (now.year - started.year) * 12 + now.month - started.month
    return now.year - started.year


def recent_cutoff(now, rule):
    months = (now.year - 1) * 12 + now.month - 1 - rule.get("months", 0)
    year, month = divmod(max(0, months), 12)
    cutoff = now.replace(year=year + 1, month=month + 1,
                         day=min(now.day, monthrange(year + 1, month + 1)[1]))
    try:
        return cutoff - timedelta(days=rule.get("days", 0) + 7 * rule.get("weeks", 0))
    except OverflowError:
        return datetime.min.replace(tzinfo=timezone.utc)


def keep_operation_ids(operations, retention, *, timezone_name, now):
    """Union rules over newest-first whole runs, using one account-local evaluation time."""
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    runs = [(operation["id"], parse_datetime(operation["started"]).astimezone(zone))
            for operation in operations]
    kept = set()
    for rule in retention["rules"]:
        kind, period = rule["type"], rule.get("period")
        if kind == "last" and not period:
            kept.update(identity for identity, _ in runs[:rule["count"]])
            continue
        if kind in {"recent", "since"}:
            cutoff = (recent_cutoff(local_now, rule) if kind == "recent" else
                      datetime.combine(date.fromisoformat(rule["date"]), datetime.min.time(), tzinfo=zone))
            cutoff = cutoff.astimezone(timezone.utc)
            for identity, started in runs:
                timestamp = started.astimezone(timezone.utc)
                if timestamp > cutoff or (kind == "recent" and timestamp == cutoff):
                    kept.add(identity)
            continue
        # Count thinning takes the latest run; calendar ranges take the first matching run.
        seen = set()
        for identity, started in (runs if kind == "last" else reversed(runs)):
            if kind == "calendar":
                if not 0 <= calendar_age(started, local_now, period) < rule["count"]:
                    continue
                if ((period == "week" and started.weekday() != rule["weekday"])
                        or (period == "month" and started.day != rule["day"])
                        or (period == "year" and started.month != rule["month"])):
                    continue
            key = period_key(started, period)
            if key not in seen:
                seen.add(key)
                kept.add(identity)
                if kind == "last" and len(seen) >= rule["count"]:
                    break
    return kept


def validate_policy(policy):
    if policy is not None:
        RetentionPolicySchema().load(policy)


def validate_context(context, repository_scope=False):
    keys = {"owner_uuid", "job_uuids", "default", "running_operation_uuids", "check_token", "timezone"}
    if repository_scope:
        keys.add("job_policies")
    if (not isinstance(context, dict) or set(context) != keys
            or not isinstance(context["job_uuids"], list) or not isinstance(context["running_operation_uuids"], list)):
        raise ValueError("Missing or incomplete backend retention context; cleanup deferred")
    context["owner_uuid"] = str(UUID(context["owner_uuid"]))
    if not isinstance(context["timezone"], str):
        raise ValueError("Missing account time zone; cleanup deferred")
    validate_timezone(context["timezone"])
    validate_policy(context["default"])
    if repository_scope:
        policies = context["job_policies"]
        if not isinstance(policies, dict) or set(policies) != set(context["job_uuids"]):
            raise ValueError("Missing or incomplete job retention policies; cleanup deferred")
        for job_uuid, policy in policies.items():
            UUID(job_uuid)
            validate_policy(policy)
    return context


class RetentionService:
    @staticmethod
    def pending_tasks(settings, repository_id):
        """Read work for the next repository run; legacy retry_at is retained only for rollback."""
        tasks = []
        for row in settings.all():
            if row["name"].startswith((f"retention_task:{repository_id}:", f"backup_completion:{repository_id}:")):
                payload = json.loads(row["settings"])
                if row["name"].startswith("backup_completion:"):
                    payload["args"]["current_operation_uuid"] = payload["operation_uuid"]
                tasks.append(payload["args"])
        return tasks

    @staticmethod
    def record_check_failure(settings, repository_id):
        settings.upsert({"name": f"retention_check_failed:{repository_id}", "settings": "100%"}, ["name"])

    @staticmethod
    def select_runs(snapshots, context, policies, current_operation_uuid, *, now):
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
            run = groups.setdefault(job, {}).setdefault(operation, {
                "id": operation, "started": started, "snapshot_ids": [], "complete": False})
            run["started"] = min(run["started"], started)
            run["snapshot_ids"].append(snapshot["id"])
            run["complete"] |= run_is_complete(snapshot)
        selected = []
        for job_uuid, job_runs in groups.items():
            runs = sorted(job_runs.values(), key=lambda run: (run["started"], run["id"]), reverse=True)
            complete = next((run for run in runs if run["complete"]), None)
            if complete is None:
                continue  # Old/unconfirmed runs cannot prove which last full backup is safe to replace.
            kept = keep_operation_ids(runs, policies[job_uuid], timezone_name=context["timezone"], now=now)
            kept.add(complete["id"])
            kept.add(current_operation_uuid)
            for run in runs:
                if run["id"] not in kept:
                    selected.extend(run["snapshot_ids"])
        return selected

    @classmethod
    def run(cls, *, repository_id, job_id, job_uuid, current_operation_uuid, current_policy,
            request_context, set_repository, resticapi, settings, send_report,
            operation_uuid=None, repository_scope=False):
        """Persist unfinished cleanup for the next run; operation_uuid reserves a solo start.

        Automatic no-policy attempts return an unpersisted command unless pending work needs acknowledgement.
        """
        task_key = f"retention_task:{repository_id}:{job_uuid or 'repository'}"
        prune_key = f"retention_prune:{repository_id}"
        pending = settings.find_one(name=task_key) or settings.find_one(
            name=f"backup_completion:{repository_id}:{current_operation_uuid}")

        def start_report():
            return AgentReport(type=AgentOperationType.retention, operation_uuid=operation_uuid,
                repository_id=repository_id, job_id=job_id, parent_operation_uuid=current_operation_uuid,
                source=AgentOperationSource.manual if operation_uuid and not current_operation_uuid else AgentOperationSource.triggered,
                data={"scope": "repository", "job_uuid": job_uuid, "cleanup_pending": True,
                      "deleted_snapshot_ids": []}).start_reporting()

        report = start_report() if operation_uuid else None
        # Retain the solo parent even if repository setup is interrupted before its first response.
        args = {"repository_id": repository_id, "job_id": job_id, "job_uuid": job_uuid,
                "current_operation_uuid": current_operation_uuid or operation_uuid, "repository_scope": repository_scope}
        settings.upsert({"name": task_key, "settings": json.dumps({"args": args, "retry_at": 0})}, ["name"])
        try:
            # A long repository check must not move calendar boundaries halfway through a pass.
            now = datetime.now(timezone.utc)
            validate_policy(current_policy)
            set_repository(repository_id)
            context = validate_context(request_context(), repository_scope)
            applicable = (any(context["job_policies"].values()) if repository_scope else current_policy)
            if not applicable and not context["default"] and not settings.find_one(name=prune_key):
                settings.delete(name=task_key)
                report = report or (start_report() if pending else AgentReport.command_report())
                report.set_data("cleanup_pending", False)
                report.log_message("Retention skipped: no applicable policy configured")
                return report.finish()
            report = report or start_report()
            # Retries of a solo run remain children of that run rather than new history entries.
            args["current_operation_uuid"] = current_operation_uuid or report.uuid
            settings.upsert({"name": task_key, "settings": json.dumps({"args": args, "retry_at": 0})}, ["name"])
            report.log_message("Running retention for repository")
            snapshots = {item["id"]: item for item in resticapi.snapshots() or []}
            context = validate_context(request_context(), repository_scope)
            owner = context["owner_uuid"]
            account_timezone = context["timezone"]
            scanned_jobs = {snapshot_uuid(snapshot, "job_uuid") for snapshot in snapshots.values()
                            if snapshot_uuid(snapshot, "drastic-owner") == owner} - {None}
            policies = dict.fromkeys(scanned_jobs - set(context["job_uuids"]), context["default"])
            if repository_scope:
                policies.update(context["job_policies"])
            elif job_uuid in context["job_uuids"]:
                policies[job_uuid] = current_policy
            selected = cls.select_runs(snapshots, context, policies, current_operation_uuid, now=now)
            report.log_message(f"Retention selected {len(selected)} expired snapshot(s)")
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
                context = validate_context(request_context(), repository_scope)
                if context["owner_uuid"] != owner or context["check_token"]:
                    raise ValueError("A repository check failure remains unresolved; cleanup deferred")
                if context["timezone"] != account_timezone:
                    raise ValueError("Account time zone changed during retention; cleanup deferred")
                settings.delete(name=f"retention_check_failed:{repository_id}")
                selected = cls.select_runs(snapshots, context, policies, current_operation_uuid, now=now)
                settings.upsert({"name": prune_key, "settings": "pending"}, ["name"])
                for start in range(0, len(selected), 1000):
                    batch = selected[start:start + 1000]
                    resticapi.forget_snapshots(batch, prune=False)
                    # Keep confirmed removals replayable if a later batch/prune or the process fails.
                    report.set_data("deleted_snapshot_ids", report.data["deleted_snapshot_ids"] + batch)
                    report.checkpoint()
                    report.log_message(f"Retention removed {len(batch)} expired snapshot(s)")
                report.log_message("Reclaiming repository space")
                report.data["prune"] = resticapi.prune()
                settings.delete(name=prune_key)
            settings.delete(name=task_key)
            report.data["cleanup_pending"] = False
            report.log_message(f"Retention finished: {len(report.data['deleted_snapshot_ids'])} snapshot(s) removed")
        except Exception as exc:
            report = report or start_report()
            args["current_operation_uuid"] = current_operation_uuid or report.uuid
            settings.upsert({"name": task_key, "settings": json.dumps({"args": args, "retry_at": 0})}, ["name"])
            report.log_message(str(exc), final_state=AgentOperationState.warning)
        return report.finish()
