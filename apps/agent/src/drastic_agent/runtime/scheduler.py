"""Durable schedule claims; all entry points share the runtime's executor."""

import logging
from concurrent.futures import CancelledError
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from drastic_agent.agent.enums import AgentReportState
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


def run_due(now, *, jobs, schedules, runs, slots, submit, run_job):
    """Claim each slot before submission, including when the server is offline."""
    utc_slot = now.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()
    once_slot = "once:" + now.replace(second=0, microsecond=0, tzinfo=None).isoformat()
    expired = [key for key, value in slots.items() if value not in {utc_slot, once_slot}]
    for key in expired:
        del slots[key]

    for job in jobs:
        for schedule in schedules.find(job_id=job["id"]):
            cron = schedule.get("cron_string")
            timing = (schedule.get("config") or {}).get("timing")
            if not timing and not cron:
                continue
            timing = timing or timing_from_cron(cron)
            if not matches(timing, now):
                continue
            planned_slot = slot_key(timing, now)
            schedule_id = schedule.get("id")
            if schedule_id is None:
                logging.warning("Ignoring schedule without id for job %s", job["id"])
                continue
            if slots.get(schedule_id) == planned_slot:
                continue
            created_at = datetime.now(timezone.utc).isoformat()
            try:
                run_id = runs.insert({
                    "schedule_id": schedule_id, "planned_slot": planned_slot,
                    "status": "claimed", "reason": None,
                    "created_at": created_at, "updated_at": created_at,
                }, ensure=False)
            except IntegrityError:
                slots[schedule_id] = planned_slot
                continue
            slots[schedule_id] = planned_slot
            logging.info("Starting job %s by schedule %s", job["id"], cron)
            try:
                future = submit(
                    run_job, job["id"], schedule["repository_id"], schedule.get("retention_id"),
                    run_options={**(schedule.get("config") or {}), "schedule_id": schedule_id},
                    resources={("job", job["id"]), ("repository", schedule["repository_id"])},
                )
            except Exception as exc:
                logging.exception("Could not submit scheduled job %s", job["id"])
                update_run(runs, run_id, "skipped", str(exc))
                continue
            if future is None:
                update_run(runs, run_id, "skipped", "execution capacity or requested resource is busy")
            else:
                update_run(runs, run_id, "started")
                if hasattr(future, "add_done_callback"):
                    future.add_done_callback(lambda completed, run_id=run_id: finish_run(runs, run_id, completed))


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
