"""Backup-chain configuration and durable, lease-fenced execution transitions."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4

from flask import current_app
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from drastic_common.agent.commands import AGENT_PROTOCOL_VERSION, AgentCommandName
from drastic_common.scheduling import describe, matches, timing_from_cron
from drastic_server.extensions import db, socketio
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.models.chain import BackupChain, BackupChainRun
from drastic_server.models.job import Job
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.services.agent import (
    AgentService,
    is_agent_conflict_response,
    is_agent_timeout_response,
)
from drastic_server.services.exceptions import ResourceConflict
from drastic_server.services.jobs.configuration import (
    assign_agent_repository,
    ensure_job_connection,
    normalize_schedule_config,
    schedule_trigger,
    validate_retention,
)
from drastic_server.services.queries import require_result

CHAIN_PROTOCOL = 11
CHAIN_DISPATCH_WORKERS = 4
TERMINAL_STEPS = {"success", "warning", "failed", "cancelled", "skipped"}


def get_chain(user_id, chain_id):
    return require_result(BackupChain.query.filter_by(id=chain_id, user_id=user_id))


def list_chains(user_id):
    return [chain_payload(chain) for chain in BackupChain.query.filter_by(user_id=user_id).order_by(BackupChain.name)]


def persist_chain(user_id, data, chain_id=None):
    chain = get_chain(user_id, chain_id) if chain_id is not None else BackupChain()
    save_chain(chain, user_id, data)
    return chain_payload(chain)


def delete_chain(user_id, chain_id):
    chain = get_chain(user_id, chain_id)
    if BackupChainRun.query.filter_by(active_chain_id=chain.id).first():
        raise ResourceConflict("Cancel and finish the active chain run before deleting the chain")
    db.session.delete(chain)
    db.session.commit()


def start_owned_chain(user_id, chain_id):
    try:
        run = start_chain(get_chain(user_id, chain_id))
    except ValueError as exc:
        raise ResourceConflict(str(exc)) from exc
    return run_payload(run)


def list_runs(user_id, chain_id, before_id=None):
    get_chain(user_id, chain_id)
    query = BackupChainRun.query.filter_by(chain_id=chain_id)
    if before_id:
        query = query.filter(BackupChainRun.id < before_id)
    return [run_payload(run) for run in query.order_by(BackupChainRun.id.desc()).limit(25)]


def cancel_run(user_id, chain_id, run_id):
    get_chain(user_id, chain_id)
    run = require_result(BackupChainRun.query.filter_by(id=run_id, chain_id=chain_id))
    if run.state == "running":
        run.cancel_requested = True
        db.session.commit()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save_chain(chain, user_id, data):
    steps = []
    for step in data["steps"]:
        job = require_result(Job.query.join(Agent).filter(Job.id == step["job_id"], Agent.user_id == user_id))
        if not CHAIN_PROTOCOL <= (job.agent.protocol_version or 0) <= AGENT_PROTOCOL_VERSION:
            raise ValueError(f"Update agent {job.agent.display_name} for backup chains (protocol {CHAIN_PROTOCOL})")
        assign_agent_repository(user_id, job.agent, step["repository_id"], data.get("recovery_key"))
        validate_retention(user_id, step["retention_id"])
        steps.append({**step, "config": normalize_schedule_config(step.get("config"))})
    chain.user_id = user_id
    chain.name = data["name"].strip()
    chain.schedules = [schedule_trigger(schedule) for schedule in data["schedules"]]
    chain.start_timeout_minutes = data["start_timeout_minutes"]
    chain.steps = steps
    db.session.add(chain)
    db.session.commit()
    return chain


def describe_steps(steps):
    result = []
    for step in steps:
        job = db.session.get(Job, step["job_id"])
        repository = db.session.get(Repository, step["repository_id"])
        retention = db.session.get(Retention, step["retention_id"]) if step.get("retention_id") else None
        result.append({**step, "job_name": job.name if job else "Deleted job",
                       "agent_id": job.agent_id if job else None,
                       "agent_name": job.agent.display_name if job else "Deleted agent",
                       "repository_name": repository.name if repository else "Deleted repository",
                       "retention_name": retention.name if retention else None})
    return result


def describe_schedules(schedules):
    return [{**schedule, "timing": schedule.get("timing") or timing_from_cron(schedule["cron_string"]),
             "cron_description": describe(schedule.get("timing") or timing_from_cron(schedule["cron_string"]))}
            for schedule in schedules]


def run_payload(run):
    steps = deepcopy(run.steps)
    for step in steps:
        if step.get("operation_uuid"):
            operation = AgentOperation.query.filter_by(uuid=step["operation_uuid"]).first()
            if operation:
                step["cleanup_pending"] = bool((operation.data or {}).get("cleanup_pending"))
    return {"id": run.id, "chain_id": run.chain_id, "state": run.state,
            "started": run.started.isoformat() + "Z", "ended": run.ended.isoformat() + "Z" if run.ended else None,
            "cancel_requested": run.cancel_requested, "steps": steps}


def chain_payload(chain):
    latest = BackupChainRun.query.filter_by(chain_id=chain.id).order_by(BackupChainRun.id.desc()).first()
    active = BackupChainRun.query.filter_by(active_chain_id=chain.id).first()
    return {"id": chain.id, "name": chain.name, "enabled": chain.enabled,
            "schedules": describe_schedules(chain.schedules),
            "start_timeout_minutes": chain.start_timeout_minutes,
            "steps": describe_steps(chain.steps), "last_run": run_payload(latest) if latest else None,
            "active_run_id": active.id if active else None}


def start_chain(chain, *, now=None, planned_slot=None):
    now = now or utcnow()
    steps = [{**step, "state": "pending", "operation_uuid": str(uuid4())}
             for step in describe_steps(chain.steps)]
    run = BackupChainRun(chain=chain, active_chain_id=chain.id, planned_slot=planned_slot,
                         steps=steps, start_timeout_minutes=chain.start_timeout_minutes,
                         started=now, state="running")
    db.session.add(run)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError("This chain already has an active run or this schedule slot was already claimed") from exc
    return run


def require_unused_chain_reference(user_id, field, reference_id):
    for chain in BackupChain.query.filter_by(user_id=user_id):
        if any(step.get(field) == reference_id for step in chain.steps):
            raise ValueError(f"Remove this item from backup chain '{chain.name}' first")
    for run in BackupChainRun.query.join(BackupChain).filter(BackupChain.user_id == user_id, BackupChainRun.state == "running"):
        if any(step.get(field) == reference_id for step in run.steps):
            raise ValueError("This item is still used by an active backup chain")


def _save_progress(run_id, token, steps, **values):
    changed = BackupChainRun.query.filter_by(id=run_id, lease_token=token).update(
        {"steps": deepcopy(steps), **values}, synchronize_session=False)
    db.session.commit()
    if not changed:
        raise RuntimeError("Backup chain lease lost")


def _finish_step_operation(step, state, reason, run_id, token):
    lease = db.session.query(BackupChainRun.id).filter_by(id=run_id, lease_token=token).exists()
    AgentOperation.query.filter(
        AgentOperation.uuid == step["operation_uuid"], AgentOperation.state == AgentOperationState.running, lease,
    ).update({"state": AgentOperationState[state], "ended": utcnow(),
              "data": {"chain_run_id": run_id, "dispatch_reason": reason}}, synchronize_session=False)
    db.session.commit()


def _fence_start(agent, step):
    """A missing status alone cannot rule out a command still in transit."""
    response = AgentService.cancel_job(agent, step["job_id"], operation_uuid=step["operation_uuid"], cancel_if_missing=True)
    return response.get("state") == AgentOperationState.success and response.get("data", {}).get("not_admitted") is True


def advance_run(run_id, *, now=None):
    """One durable transition per tick; database leases also cover multiple workers."""
    now = now or utcnow()
    token = str(uuid4())
    claimed = BackupChainRun.query.filter(
        BackupChainRun.id == run_id, BackupChainRun.state == "running",
        or_(BackupChainRun.lease_until.is_(None), BackupChainRun.lease_until <= now),
    ).update({"lease_until": now + timedelta(minutes=5), "lease_token": token}, synchronize_session=False)
    db.session.commit()
    if not claimed:
        return
    try:
        db.session.expire_all()
        run = db.session.get(BackupChainRun, run_id)
        steps = deepcopy(run.steps)
        step = next((item for item in steps if item["state"] not in TERMINAL_STEPS), None)
        if step:
            operation = AgentOperation.query.filter_by(uuid=step["operation_uuid"]).first()
            if operation and operation.ended and operation.state != AgentOperationState.running:
                step.update(state="skipped" if (operation.data or {}).get("start_skipped") else operation.state.name, operation_id=operation.id,
                            cleanup_pending=bool((operation.data or {}).get("cleanup_pending")))
                if (operation.data or {}).get("start_skipped"):
                    step["reason"] = operation.data.get("start_skip_reason")
                _save_progress(run_id, token, steps)
                return
        if not step or (run.cancel_requested and step["state"] in {"pending", "waiting"}):
            if run.cancel_requested:
                for item in steps:
                    if item["state"] not in TERMINAL_STEPS:
                        item.update(state="cancelled", reason="Chain cancellation requested")
                        _finish_step_operation(item, "cancelled", item["reason"], run_id, token)
                state = "cancelled"
            elif any(item["state"] in {"failed", "skipped", "cancelled"} for item in steps):
                state = "failed"
            elif any(item["state"] == "warning" for item in steps):
                state = "warning"
            else:
                state = "success"
            _save_progress(run_id, token, steps, state=state, ended=now, active_chain_id=None)
            return
        job = db.session.get(Job, step["job_id"])
        repository = db.session.get(Repository, step["repository_id"])
        user_id = run.chain.user_id
        if not job or job.agent.user_id != user_id or not repository or repository.user_id != user_id:
            step.update(state="skipped", reason="Job, agent or repository no longer available")
            _save_progress(run_id, token, steps)
            return
        agent = job.agent
        if step["state"] == "pending":
            step.update(state="waiting", start_deadline=(now + timedelta(minutes=run.start_timeout_minutes)).isoformat() + "Z")
            _save_progress(run_id, token, steps)
        deadline = datetime.fromisoformat(step["start_deadline"]).replace(tzinfo=None)
        if run.cancel_requested:
            if not agent.online:
                return  # Cancellation must reach the agent before freeing the active run.
            response = AgentService.cancel_job(agent, job.id, operation_uuid=step["operation_uuid"], cancel_if_missing=True)
            step["reason"] = response.get("log") or "Waiting for cancellation report"
            _save_progress(run_id, token, steps)
            return
        if step["state"] == "running":
            return  # No runtime timeout: lost connectivity does not mean a job ended.
        if step["state"] == "dispatching":
            if not agent.online:
                return  # An unknown dispatch must never be treated as a skipped job.
            response = AgentService.send_command(agent, AgentCommandName.get_operation_status,
                                                 operation_uuid=step["operation_uuid"], timeout=5)
            if response.get("state") != AgentOperationState.success:
                return
            if response.get("data", {}).get("known"):
                step["state"] = "running"
                _save_progress(run_id, token, steps)
                return
            if deadline <= now:
                # Fence delayed commands on the agent, even if its clock differs.
                if not _fence_start(agent, step):
                    return
                step["state"] = "waiting"
        if deadline <= now:
            step.update(state="skipped", reason="Start timeout: agent offline or resources unavailable")
            _finish_step_operation(step, "failed", step["reason"], run_id, token)
            _save_progress(run_id, token, steps)
            return
        if not agent.online:
            step["reason"] = "Agent offline"
            _save_progress(run_id, token, steps)
            return
        try:
            if not CHAIN_PROTOCOL <= (agent.protocol_version or 0) <= AGENT_PROTOCOL_VERSION:
                raise ValueError(f"Update agent for backup chains (protocol {CHAIN_PROTOCOL})")
            ensure_job_connection(agent, job.type.name, job.config)
            if not any(item.id == repository.id for item in agent.repositories):
                raise ValueError("Repository is no longer assigned to agent")
            if step.get("retention_id"):
                retention = db.session.get(Retention, step["retention_id"])
                if not retention or retention.user_id != user_id:
                    raise ValueError("Retention policy no longer available")
        except ValueError as exc:
            if step["state"] == "dispatching" and not _fence_start(agent, step):
                return
            step.update(state="skipped", reason=str(exc))
            _finish_step_operation(step, "failed", step["reason"], run_id, token)
            _save_progress(run_id, token, steps)
            return
        # Synchronize before admission, including chain-only retention policies.
        response = AgentService.sync(agent, await_response=True)
        if response.get("state") != AgentOperationState.success:
            step["reason"] = response.get("log") or "Agent synchronization failed"
            _save_progress(run_id, token, steps)
            return
        db.session.refresh(run)
        if run.cancel_requested:
            return
        operation = AgentOperation.query.filter_by(uuid=step["operation_uuid"]).first()
        if not operation:
            operation = AgentOperation(uuid=step["operation_uuid"], agent=agent, job=job, repository=repository,
                                       retention_id=step.get("retention_id"), type=AgentOperationType.backup,
                                       source=AgentOperationSource.triggered, state=AgentOperationState.running,
                                       started=now, data={"chain_run_id": run.id})
            db.session.add(operation)
            db.session.commit()
        was_uncertain = step["state"] == "dispatching"
        step.update(state="dispatching", operation_id=operation.id, reason="Waiting for start confirmation")
        _save_progress(run_id, token, steps)  # Commit intent before sending, including the UUID.
        response = AgentService.run_job(agent, job.id, repository.id, retention_id=step.get("retention_id"),
                                        operation_uuid=step["operation_uuid"],
                                        run_options={**step["config"], "chain_run_id": run.id, "start_deadline": step["start_deadline"]})
        if response.get("state") == AgentOperationState.success:
            step.update(state="running", reason=None)
        elif not is_agent_timeout_response(response):
            if is_agent_conflict_response(response) or response.get("log") == "Agent is offline":
                step.update(state="dispatching" if was_uncertain else "waiting", reason=response.get("log"))
            else:
                if was_uncertain and not _fence_start(agent, step):
                    return
                step.update(state="skipped", reason=response.get("log") or "Agent rejected backup")
                _finish_step_operation(step, "failed", step["reason"], run_id, token)
        _save_progress(run_id, token, steps)
    finally:
        db.session.rollback()
        BackupChainRun.query.filter_by(id=run_id, lease_token=token).update(
            {"lease_until": None, "lease_token": None}, synchronize_session=False)
        db.session.commit()


def tick(*, now=None):
    """Persist due slots without waiting for any agent network requests."""
    now = now or utcnow()
    slot = now.replace(second=0, microsecond=0)
    for chain in BackupChain.query.all():
        if (not any(schedule["enabled"] and matches(schedule.get("timing") or timing_from_cron(schedule["cron_string"]),
                                                   now.replace(tzinfo=timezone.utc)) for schedule in chain.schedules)
                or BackupChainRun.query.filter_by(chain_id=chain.id, planned_slot=slot).first()):
            continue
        try:
            start_chain(chain, now=now, planned_slot=slot)
        except ValueError:
            # Record a busy occurrence; never build up catch-up runs.
            if not BackupChainRun.query.filter_by(chain_id=chain.id, planned_slot=slot).first():
                skipped = BackupChainRun(chain=chain, planned_slot=slot, started=now, ended=now,
                                         state="skipped", start_timeout_minutes=chain.start_timeout_minutes,
                                         steps=[{**item, "state": "skipped", "reason": "Previous chain run still active"} for item in describe_steps(chain.steps)])
                db.session.add(skipped)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()


def _advance_in_background(app, run_id):
    with app.app_context():
        try:
            advance_run(run_id)
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Backup chain run %s dispatch failed", run_id)


def submit_pending_runs(app, executor, pending):
    """Bound both workers and queued work; avoid submitting a run twice locally."""
    for run_id, future in list(pending.items()):
        if future.done():
            del pending[run_id]
    capacity = CHAIN_DISPATCH_WORKERS - len(pending)
    if capacity <= 0:
        return
    query = db.session.query(BackupChainRun.id).filter(
        BackupChainRun.state == "running", BackupChainRun.id.notin_(list(pending)),
        or_(BackupChainRun.lease_until.is_(None), BackupChainRun.lease_until <= utcnow()),
    )
    # Lease/progress updates refresh `updated`: untouched runs get a turn first.
    for (run_id,) in query.order_by(BackupChainRun.updated, BackupChainRun.id).limit(capacity).all():
        pending[run_id] = executor.submit(_advance_in_background, app, run_id)


def install_dispatcher(app):
    start_lock = Lock()
    started = False

    def dispatch():
        pending = {}
        with ThreadPoolExecutor(max_workers=CHAIN_DISPATCH_WORKERS, thread_name_prefix="backup-chain") as executor:
            while True:
                try:
                    with app.app_context():
                        tick()
                        submit_pending_runs(app, executor, pending)
                except Exception:
                    app.logger.exception("Backup chain dispatcher failed")
                socketio.sleep(5)

    @app.before_request
    def start():
        nonlocal started
        if app.testing or started:
            return
        with start_lock:
            if not started:
                started = True
                socketio.start_background_task(dispatch)

    app.extensions["backup_chain_dispatcher"] = start
