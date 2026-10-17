"""Durable schedule claims; pending retries exist only for this agent process."""

import logging
from concurrent.futures import CancelledError
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from drastic_agent.agent.enums import AgentReportState
from drastic_common.backup_retry import BackupRetrySchema, retry_allowed
from drastic_common.scheduling import matches, slot_key, timing_from_cron


def update_run(runs, run_id, status, reason=None):
    runs.update({"id": run_id, "status": status, "reason": reason,
                 "updated_at": datetime.now(timezone.utc).isoformat()}, ["id"], ensure=False)


def finish_run(runs, run_id, future):
    try:
        report = future.result()
    except CancelledError:
        update_run(runs, run_id, "failed", "execution was cancelled")
        return
    except Exception as exc:
        logging.exception("Scheduled operation failed")
        update_run(runs, run_id, "failed", str(exc))
        return
    if getattr(report, "state", None) in {AgentReportState.failed, AgentReportState.cancelled}:
        update_run(runs, run_id, "failed", getattr(report, "log", None) or "operation reported failure")
    else:
        update_run(runs, run_id, "finished")
    return report


def advance_run(task, now, runs, schedules, submit, run_job):
    """Poll futures on the scheduler thread; waiting never occupies an executor worker."""
    options = task["options"]
    if task["future"] is None:
        if task["next_at"] is not None:
            schedule = schedules.find_one(id=options["schedule_id"])
            if (not schedule or schedule.get("enabled") is False
                    or schedule["repository_id"] != task["repository_id"]):
                return False
            if now < task["next_at"]:
                return True
        try:
            task["future"] = submit(run_job, task["job_id"], task["repository_id"],
                                    run_options=dict(options), resources={
                                        ("job", task["job_id"]), ("repository", task["repository_id"])})
        except Exception as exc:
            logging.exception("Could not submit scheduled job %s", task["job_id"])
            update_run(runs, task["run_id"], "failed", str(exc))
            return False
        if task["future"] is None:
            if task["next_at"] is not None:
                return True  # Busy resources do not consume another attempt.
            update_run(runs, task["run_id"], "skipped", "execution capacity or requested resource is busy")
            return False
        update_run(runs, task["run_id"], "started")
    if not task["future"].done():
        return True
    report = finish_run(runs, task["run_id"], task["future"])
    if report and retry_allowed(options["retry"], options["attempt"], report.state.name, report.data):
        task.update(future=None, next_at=report.ended + timedelta(minutes=options["retry"]["delay_minutes"]))
        options["attempt"] += 1
        return True
    return False


def run_due(now, *, jobs, schedules, runs, slots, pending, submit, run_job):
    """Claim each slot before submission, including when the server is offline."""
    utc_now = now.astimezone(timezone.utc)
    for schedule_id, task in list(pending.items()):
        if not advance_run(task, utc_now, runs, schedules, submit, run_job):
            del pending[schedule_id]
    utc_slot = utc_now.replace(second=0, microsecond=0).isoformat()
    once_slot = "once:" + now.replace(second=0, microsecond=0, tzinfo=None).isoformat()
    for key in [key for key, value in slots.items() if value not in {utc_slot, once_slot}]:
        del slots[key]
    for job in jobs:
        for schedule in schedules.find(job_id=job["id"]):
            config = schedule.get("config") or {}
            if not config.get("timing") and not schedule.get("cron_string"):
                continue
            timing = config.get("timing") or timing_from_cron(schedule["cron_string"])
            if schedule.get("enabled") is False or not matches(timing, now):
                continue
            schedule_id, planned_slot = schedule.get("id"), slot_key(timing, now)
            if schedule_id is None or slots.get(schedule_id) == planned_slot:
                continue
            created = datetime.now(timezone.utc).isoformat()
            try:
                run_id = runs.insert({"schedule_id": schedule_id, "planned_slot": planned_slot, "status": "claimed",
                                      "reason": None, "created_at": created, "updated_at": created}, ensure=False)
            except IntegrityError:
                slots[schedule_id] = planned_slot
                continue
            slots[schedule_id] = planned_slot
            if schedule_id in pending:
                update_run(runs, run_id, "skipped", "Previous scheduled run still active")
                continue
            task = {"run_id": run_id, "job_id": job["id"], "repository_id": schedule["repository_id"],
                    "future": None, "next_at": None,
                    "options": {**config, "schedule_id": schedule_id, "attempt": 1,
                                "retry": BackupRetrySchema().load(config.get("retry") or {})}}
            if advance_run(task, utc_now, runs, schedules, submit, run_job):
                pending[schedule_id] = task


def recover_runs(runs):
    """Interrupted claims remain consumed, rather than silently replaying backups."""
    for status in ("claimed", "started"):
        for run in list(runs.find(status=status)):
            update_run(runs, run["id"], "failed", "agent stopped before scheduled execution completed")


def prune_runs(runs, now=None):
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=90)
    for status in ("finished", "failed", "skipped"):
        for run in list(runs.find(status=status)):
            if str(run.get("planned_slot", "")).startswith("once:"):
                continue  # One-shot claims must survive history cleanup and clock changes.
            try:
                updated_at = datetime.fromisoformat(str(run["updated_at"]).replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                continue
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if updated_at < cutoff:
                runs.delete(id=run["id"])
