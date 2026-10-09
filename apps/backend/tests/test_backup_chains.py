from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Event, Lock

import pytest
from marshmallow import ValidationError

from drastic_server.app import create_app
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentOperation, AgentOperationState, AgentSession
from drastic_server.models.chain import BackupChain, BackupChainRun
from drastic_server.models.job import Job, JobType
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.models.user import User
from drastic_server.schemas.chain import ChainInputSchema
from drastic_server.services.agent import AgentService
from drastic_server.services.chains import (
    advance_run,
    chain_payload,
    start_chain,
    submit_pending_runs,
    tick,
)
from drastic_server.services.operations.reports import AgentOperationService

NOW = datetime(2026, 10, 7, 2, 0)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_APP_MASTER_SECRET", "test-master-secret")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_DATABASE_URI", f"sqlite:///{tmp_path / 'chains.db'}")
    monkeypatch.setenv("DRASTIC_JWT_COOKIE_CSRF_PROTECT", "false")
    app = create_app()
    app.testing = True
    with app.app_context():
        db.create_all()
        user = User(name="admin")
        user.set_initial_password("account-password", recovery_key="user-recovery-key")
        agents = [Agent(user=user, secret="secret", alias=f"Agent {i}", protocol_version=11) for i in range(2)]
        for i, agent in enumerate(agents):
            AgentSession(agent=agent, request_sid=f"sid-{i}")
        repository = Repository(user=user, name="Storage", kind="custom", location="/backup")
        retention = Retention(user=user, name="Keep two", keep_last=2)
        jobs = [Job(agent=agent, name=f"Files {i}", type=JobType.file, config={"paths": []}) for i, agent in enumerate(agents)]
        for agent in agents:
            agent.repositories.append(repository)
        db.session.add_all([user, *agents, repository, retention, *jobs])
        db.session.flush()
        chain = BackupChain(user_id=user.id, name="Nightly", schedules=[{"enabled": True, "cron_string": "0 2 * * *"}],
                            start_timeout_minutes=1, steps=[{"job_id": job.id, "repository_id": repository.id,
                                                            "retention_id": retention.id, "config": {}} for job in jobs])
        db.session.add(chain)
        db.session.commit()
        calls = []
        monkeypatch.setattr(AgentService, "sync", lambda *args, **kwargs: {"state": AgentOperationState.success})

        def run_job(agent, job_id, repository_id, **kwargs):
            calls.append((agent.id, job_id, repository_id, kwargs))
            return {"state": AgentOperationState.success}

        monkeypatch.setattr(AgentService, "run_job", run_job)
        yield app, chain, jobs, calls
        db.session.remove()
        db.drop_all()


def finish_step(run, jobs, index, state="success", data=None):
    step = run.steps[index]
    AgentOperationService(jobs[index].agent).ingest({
        "uuid": step["operation_uuid"], "type": "backup", "state": state,
        "job_id": jobs[index].id, "repository_id": step["repository_id"],
        "started": NOW.isoformat(), "ended": (NOW + timedelta(seconds=10)).isoformat(),
        "data": data or {},
    })


@pytest.mark.parametrize("same_job", [False, True])
def test_serial_order_retention_and_failure_continuation_survive_sessions(setup, same_job):
    _, chain, jobs, calls = setup
    if same_job:
        jobs = [jobs[0], jobs[0]]
        repository = Repository(user=jobs[0].agent.user, name="Offsite", kind="custom", location="/offsite")
        db.session.add(repository)
        jobs[0].agent.repositories.append(repository)
        db.session.flush()
        chain.steps = [chain.steps[0], {**chain.steps[0], "repository_id": repository.id, "retention_id": None}]
        db.session.commit()
    expected_targets = [(step["repository_id"], step["retention_id"]) for step in chain.steps]
    run = start_chain(chain, now=NOW)
    run_id = run.id
    advance_run(run_id, now=NOW)
    assert len(calls) == 1
    assert calls[0][3]["retention_id"] == chain.steps[0]["retention_id"]
    advance_run(run_id, now=NOW)
    assert len(calls) == 1
    finish_step(db.session.get(BackupChainRun, run_id), jobs, 0, "failed")
    job_ids = [job.id for job in jobs]
    db.session.remove()  # Dispatcher state must not depend on an in-memory session.
    jobs = [db.session.get(Job, job_id) for job_id in job_ids]
    advance_run(run_id, now=NOW)
    advance_run(run_id, now=NOW)
    assert [call[1] for call in calls] == [job.id for job in jobs]
    assert [(call[2], call[3]["retention_id"]) for call in calls] == expected_targets
    finish_step(db.session.get(BackupChainRun, run_id), jobs, 1)
    advance_run(run_id, now=NOW)
    advance_run(run_id, now=NOW)
    assert db.session.get(BackupChainRun, run_id).state == "failed"
    assert db.session.get(BackupChainRun, run_id).active_chain_id is None


def test_lost_confirmation_reuses_uuid_and_never_skips_unknown_dispatch(setup, monkeypatch):
    _, chain, jobs, calls = setup
    run = start_chain(chain, now=NOW)
    sent = []

    def timeout(*args, **kwargs):
        sent.append(kwargs["operation_uuid"])
        return {"data": {"timeout": True}, "state": AgentOperationState.running}

    monkeypatch.setattr(AgentService, "run_job", timeout)
    monkeypatch.setattr(AgentService, "send_command", lambda *args, **kwargs: {"state": AgentOperationState.success, "data": {"known": False}})
    advance_run(run.id, now=NOW)
    advance_run(run.id, now=NOW + timedelta(seconds=20))
    assert sent == [run.steps[0]["operation_uuid"]] * 2
    monkeypatch.setattr(AgentService, "send_command", lambda *args, **kwargs: {"state": AgentOperationState.success, "data": {"known": True}})
    advance_run(run.id, now=NOW + timedelta(minutes=2))
    db.session.refresh(run)
    assert run.steps[0]["state"] == "running"
    assert run.steps[1]["state"] == "pending"
    assert calls == []


def test_offline_step_times_out_then_next_agent_runs(setup):
    _, chain, jobs, calls = setup
    db.session.delete(jobs[0].agent.session)
    db.session.commit()
    run = start_chain(chain, now=NOW)
    advance_run(run.id, now=NOW)
    advance_run(run.id, now=NOW + timedelta(minutes=2))
    advance_run(run.id, now=NOW + timedelta(minutes=2))
    db.session.refresh(run)
    assert run.steps[0]["state"] == "skipped"
    assert [call[1] for call in calls] == [jobs[1].id]


def test_unknown_start_timeout_fences_delayed_command_before_skipping(setup, monkeypatch):
    _, chain, _, _ = setup
    run = start_chain(chain, now=NOW)
    monkeypatch.setattr(AgentService, "run_job", lambda *args, **kwargs: {"state": AgentOperationState.running, "data": {"timeout": True}})
    advance_run(run.id, now=NOW)
    monkeypatch.setattr(AgentService, "send_command", lambda *args, **kwargs: {"state": AgentOperationState.success, "data": {"known": False}})
    fenced = []
    monkeypatch.setattr(AgentService, "cancel_job", lambda *args, **kwargs: fenced.append(kwargs) or {"state": AgentOperationState.success, "data": {"not_admitted": True}})
    advance_run(run.id, now=NOW + timedelta(minutes=2))
    db.session.refresh(run)
    assert fenced[0]["operation_uuid"] == run.steps[0]["operation_uuid"]
    assert run.steps[0]["state"] == "skipped"


def test_schedule_slots_no_catchup_overlap_and_run_snapshot(setup):
    _, chain, _, _ = setup
    tick(now=NOW)
    tick(now=NOW + timedelta(seconds=5))
    assert BackupChainRun.query.count() == 1
    original = deepcopy(BackupChainRun.query.first().steps)
    chain.steps = list(reversed(chain.steps))
    db.session.commit()
    tick(now=NOW + timedelta(days=1))
    assert BackupChainRun.query.count() == 2
    assert BackupChainRun.query.order_by(BackupChainRun.id.desc()).first().state == "skipped"
    assert BackupChainRun.query.first().steps[0]["job_id"] == original[0]["job_id"]
    with pytest.raises(ValueError, match="active run"):
        start_chain(chain, now=NOW)


def test_lease_blocks_second_dispatcher(setup):
    _, chain, _, calls = setup
    run = start_chain(chain, now=NOW)
    run.lease_until = NOW + timedelta(minutes=5)
    run.lease_token = "another-worker"
    db.session.commit()
    advance_run(run.id, now=NOW)
    assert calls == []
    advance_run(run.id, now=NOW + timedelta(minutes=6))
    assert len(calls) == 1


def test_cancellation_waits_for_active_job_and_stops_followups(setup, monkeypatch):
    _, chain, jobs, calls = setup
    run = start_chain(chain, now=NOW)
    advance_run(run.id, now=NOW)
    run.cancel_requested = True
    db.session.commit()
    cancels = []
    monkeypatch.setattr(AgentService, "cancel_job", lambda *args, **kwargs: cancels.append(kwargs) or {"state": AgentOperationState.success})
    advance_run(run.id, now=NOW)
    assert cancels[0]["cancel_if_missing"] is True
    db.session.refresh(run)
    assert run.state == "running"
    finish_step(run, jobs, 0, "cancelled")
    advance_run(run.id, now=NOW)
    advance_run(run.id, now=NOW)
    db.session.refresh(run)
    assert run.state == "cancelled"
    assert run.steps[1]["state"] == "cancelled"
    assert len(calls) == 1


def test_cleanup_retry_updates_parent_without_hiding_original_warning(setup):
    _, chain, jobs, _ = setup
    run = start_chain(chain, now=NOW)
    advance_run(run.id, now=NOW)
    finish_step(run, jobs, 0, "warning", {"cleanup_pending": True})
    AgentOperationService(jobs[0].agent).ingest({
        "uuid": "retention-retry", "type": "retention", "state": "success",
        "parent_operation_uuid": run.steps[0]["operation_uuid"],
        "job_id": jobs[0].id, "repository_id": run.steps[0]["repository_id"],
        "started": NOW.isoformat(), "ended": (NOW + timedelta(minutes=3)).isoformat(),
    })
    operation = AgentOperation.query.filter_by(uuid=run.steps[0]["operation_uuid"]).one()
    assert operation.state == AgentOperationState.warning
    assert operation.data["cleanup_pending"] is False
    assert chain_payload(chain)["last_run"]["steps"][0]["cleanup_pending"] is False
    for uuid, started, pending in (("older-warning", NOW - timedelta(hours=1), False),
                                   ("new-warning", NOW + timedelta(hours=1), True)):
        AgentOperationService(jobs[0].agent).ingest({
            "uuid": uuid, "type": "backup", "state": "warning", "job_id": jobs[0].id,
            "repository_id": run.steps[0]["repository_id"], "started": started.isoformat(),
            "ended": (started + timedelta(seconds=10)).isoformat(), "data": {"cleanup_pending": True},
        })
        assert AgentOperation.query.filter_by(uuid=uuid).one().data["cleanup_pending"] is pending


@pytest.mark.parametrize("prune_pending", [False, True])
def test_retired_policy_reports_clear_only_completed_cleanup(setup, prune_pending):
    _, chain, jobs, _ = setup
    run = start_chain(chain, now=NOW)
    advance_run(run.id, now=NOW)
    finish_step(run, jobs, 0, "warning", {"cleanup_pending": True})
    payload = {
        "uuid": "retired-retention", "type": "retention", "state": "warning", "retention_id": None,
        "parent_operation_uuid": run.steps[0]["operation_uuid"], "job_id": jobs[0].id,
        "repository_id": run.steps[0]["repository_id"], "started": NOW.isoformat(),
        "ended": (NOW + timedelta(minutes=3)).isoformat(),
        "data": {"retired_policy_id": 123, "cleanup_pending": prune_pending},
    }
    AgentOperationService(jobs[0].agent).ingest(payload)
    backup = AgentOperation.query.filter_by(uuid=run.steps[0]["operation_uuid"]).one()
    assert backup.state == AgentOperationState.warning
    assert backup.data["retention_state"] == "warning"
    assert backup.data["cleanup_pending"] is prune_pending
    if prune_pending:
        AgentOperationService(jobs[0].agent).ingest({
            **payload, "uuid": "prune-only-retry", "state": "success", "data": {"prune": {}},
            "ended": (NOW + timedelta(minutes=20)).isoformat(),
        })
        assert backup.data["cleanup_pending"] is False
        assert backup.state == AgentOperationState.warning


def test_api_membership_ownership_and_active_deletion(setup):
    app, chain, jobs, _ = setup
    client = app.test_client()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "account-password"}).status_code == 200
    response = client.get("/api/chains/")
    assert response.status_code == 200
    assert response.json[0]["steps"][1]["agent_id"] == jobs[1].agent_id
    memberships = client.get(f"/api/jobs/{jobs[1].id}").json["chains"]
    assert memberships[0]["position"] == 2
    assert memberships[0]["previous_job_name"] == jobs[0].name
    assert client.delete(f"/api/jobs/{jobs[0].id}").status_code == 409
    assert client.post(f"/api/chains/{chain.id}/run").status_code == 202
    assert client.post(f"/api/chains/{chain.id}/run").status_code == 409
    assert client.delete(f"/api/chains/{chain.id}").status_code == 409
    other = User(name="other")
    other.set_initial_password("password-other", recovery_key="other-key")
    db.session.add(other)
    db.session.commit()
    outsider = app.test_client()
    outsider.post("/api/auth/login", json={"username": "other", "password": "password-other"})
    assert outsider.get("/api/chains/").json == []
    assert outsider.get(f"/api/chains/{chain.id}/runs").status_code == 404
    assert outsider.post(f"/api/chains/{chain.id}/run").status_code == 404


def test_chain_input_rejects_duplicate_targets_and_invalid_times():
    data = {"name": "Nightly", "schedules": [{"enabled": True, "hour": "2", "minute": "0", "day_of_week": [1]}],
            "steps": [{"job_id": 1, "repository_id": 2}]}
    assert ChainInputSchema().load(data)["start_timeout_minutes"] == 60
    with pytest.raises(ValidationError, match="once per repository"):
        ChainInputSchema().load({**data, "steps": data["steps"] * 2})
    steps = data["steps"] + [{"job_id": 1, "repository_id": 3}]
    assert len(ChainInputSchema().load({**data, "steps": steps})["steps"]) == 2
    with pytest.raises(ValidationError):
        ChainInputSchema().load({**data, "schedules": [{**data["schedules"][0], "hour": "25"}]})
    with pytest.raises(ValidationError):
        ChainInputSchema().load({**data, "schedules": [{**data["schedules"][0], "day_of_week": [1, 1]}]})
    assert ChainInputSchema().load({**data, "schedules": []})["schedules"] == []


def test_chain_api_create_edit_and_start_preserves_saved_step_options(setup):
    app, chain, jobs, _ = setup
    client = app.test_client()
    client.post("/api/auth/login", json={"username": "admin", "password": "account-password"})
    steps = deepcopy(chain.steps)
    steps[0]["config"] = {"repository_check": {"enabled": True, "read_data": "100%"}}
    payload = {"name": "Another chain", "schedules": [{"enabled": True, "hour": "3", "minute": "15", "day_of_week": [1, 5]},
                                                     {"enabled": False, "hour": "5", "minute": "30", "day_of_week": [0, 6]}],
               "start_timeout_minutes": 30, "steps": steps}
    response = client.post("/api/chains/", json=payload)
    assert response.status_code == 201, response.json
    chain_id = response.json["id"]
    assert response.json["schedules"][0]["cron_string"] == "15 3 * * 1,5"
    assert response.json["schedules"][1]["cron_string"] == "30 5 * * 0,6"
    assert response.json["schedules"][1]["enabled"] is False
    assert response.json["steps"][0]["config"]["repository_check"]["read_data"] == "100%"
    payload["steps"].reverse()
    response = client.put(f"/api/chains/{chain_id}", json=payload)
    assert response.status_code == 200
    assert response.json["steps"][0]["job_id"] == jobs[1].id
    started = client.post(f"/api/chains/{chain_id}/run")
    assert started.status_code == 202
    assert started.json["steps"][1]["config"]["repository_check"]["read_data"] == "100%"


def test_multiple_schedules_coalesce_matching_slots_and_disable_independently(setup, monkeypatch):
    _, chain, _, _ = setup
    # Keep this test focused on schedule admission rather than agent execution.
    monkeypatch.setattr("drastic_server.services.chains.advance_run", lambda *args, **kwargs: None)
    chain.schedules = [{"enabled": True, "cron_string": "0 2 * * *"},
                       {"enabled": True, "cron_string": "0 2 * * 3"},
                       {"enabled": False, "cron_string": "0 4 * * *"}]
    db.session.commit()
    tick(now=NOW)  # Wednesday matches two schedules, but starts one run.
    tick(now=NOW + timedelta(seconds=5))
    assert BackupChainRun.query.count() == 1
    run = BackupChainRun.query.one()
    run.state = "success"
    run.active_chain_id = None
    run.ended = NOW
    db.session.commit()
    tick(now=NOW + timedelta(hours=2))
    assert BackupChainRun.query.count() == 1
    chain.schedules = [{"enabled": False, "cron_string": "0 2 * * *"},
                       {"enabled": True, "cron_string": "0 4 * * *"}]
    db.session.commit()
    tick(now=NOW + timedelta(days=1))
    assert BackupChainRun.query.count() == 1
    tick(now=NOW + timedelta(days=1, hours=2))
    assert BackupChainRun.query.count() == 2


def test_chain_without_schedules_still_runs_manually(setup):
    _, chain, _, _ = setup
    chain.schedules = []
    db.session.commit()
    tick(now=NOW)
    assert BackupChainRun.query.count() == 0
    assert chain_payload(chain)["enabled"] is False
    assert chain_payload(chain)["schedules"] == []
    assert start_chain(chain, now=NOW).state == "running"


def test_typed_chain_schedule_and_preview_use_same_rules(setup, monkeypatch):
    app, chain, _, _ = setup
    client = app.test_client()
    client.post("/api/auth/login", json={"username": "admin", "password": "account-password"})
    timing = {"type": "periodic", "interval": 90, "offset": 0}
    payload = {"name": "Periodic", "schedules": [{"enabled": True, "timing": timing}], "steps": chain.steps}
    result = client.put(f"/api/chains/{chain.id}", json=payload)
    assert result.status_code == 200, result.json
    assert result.json["schedules"][0]["timing"] == timing
    assert result.json["schedules"][0]["cron_string"] == ""
    result = client.post('/api/jobs/schedules/preview', json={"timing": timing})
    assert result.status_code == 200
    assert result.json["next_run"] and result.json["timezone"] == "UTC"
    assert "90 minutes" in result.json["description"]
    monkeypatch.setattr("drastic_server.services.chains.advance_run", lambda *args, **kwargs: None)
    tick(now=NOW)  # 02:00 is not on the 90-minute raster.
    assert BackupChainRun.query.count() == 0
    tick(now=NOW + timedelta(hours=1))
    assert BackupChainRun.query.count() == 1
    run = BackupChainRun.query.one()
    run.state, run.active_chain_id, run.ended = 'success', None, NOW
    chain.schedules = [{"enabled": True, "timing": {"type": "once", "date": "2026-10-08", "hour": 2, "minute": 0}, "cron_string": ""}]
    db.session.commit()
    tick(now=NOW + timedelta(days=1))
    tick(now=NOW + timedelta(days=1, seconds=10))
    tick(now=NOW + timedelta(days=2))
    assert BackupChainRun.query.count() == 2


def test_job_typed_schedule_persists_and_syncs_without_becoming_recurring(setup):
    from drastic_server.models.job import JobSchedule
    from drastic_server.services.agent.request import AgentRequestService

    app, chain, jobs, _ = setup
    jobs[0].agent.protocol_version = 12
    db.session.commit()
    client = app.test_client()
    client.post("/api/auth/login", json={"username": "admin", "password": "account-password"})
    timing = {"type": "once", "date": "2027-02-01", "hour": 2, "minute": 0}
    result = client.post(f'/api/jobs/{jobs[0].id}/schedules', json={
        "enabled": True, "timing": timing, "repository_id": chain.steps[0]['repository_id'],
        "retention_id": chain.steps[0]['retention_id'],
    })
    assert result.status_code == 201, result.json
    schedule = db.session.get(JobSchedule, result.json['id'])
    assert schedule.config['timing'] == timing
    assert schedule.cron_string == ''
    synced = AgentRequestService(jobs[0].agent).sync()['schedules'][0]
    assert synced['config']['timing'] == timing
    assert synced['cron_string'] == ''
    detail = client.get(f'/api/jobs/{jobs[0].id}').json['schedules'][0]
    assert detail['timing'] == timing
    assert '2027-02-01' in detail['cron_description']
    invalid = client.post('/api/jobs/schedules/preview', json={'timing': {'type': 'periodic', 'interval': 0, 'offset': 0}})
    assert invalid.status_code == 422


def test_dispatcher_can_start_without_http_visit_and_only_starts_once(setup, monkeypatch):
    from drastic_server.extensions import socketio

    app, _, _, _ = setup
    tasks = []
    monkeypatch.setattr(socketio, "start_background_task", lambda target: tasks.append(target))
    app.testing = False
    app.extensions["backup_chain_dispatcher"]()
    app.extensions["backup_chain_dispatcher"]()
    assert len(tasks) == 1
    app.testing = True


def test_retention_changes_sync_chain_only_and_pending_cleanup_agents(setup, monkeypatch):
    from drastic_server.models.job import JobSchedule
    from drastic_server.services.agent.request import AgentRequestService

    app, chain, jobs, _ = setup
    cleanup_agent = Agent(user_id=chain.user_id, secret="cleanup-agent")
    AgentSession(agent=cleanup_agent, request_sid="cleanup-sid")
    offline_agent = Agent(user_id=chain.user_id, secret="offline-agent")
    other_user = User(name="unrelated")
    other_user.set_initial_password("other-password", recovery_key="other-recovery-key")
    other_agent = Agent(user=other_user, secret="other-agent")
    AgentSession(agent=other_agent, request_sid="other-sid")
    db.session.add_all([cleanup_agent, offline_agent, other_agent])
    db.session.commit()
    assert JobSchedule.query.count() == 0
    expected = {jobs[0].agent_id, jobs[1].agent_id, cleanup_agent.id}
    retention_id = chain.steps[0]["retention_id"]
    received = {}

    def sync(agent):
        received[agent.id] = AgentRequestService(agent).sync()["retentions"]

    monkeypatch.setattr(AgentService, "sync", sync)
    client = app.test_client()
    client.post("/api/auth/login", json={"username": "admin", "password": "account-password"})
    response = client.put(f"/api/retentions/{retention_id}", json={"keep_last": 10})
    assert response.status_code == 200, response.json
    assert set(received) == expected
    assert all(any(policy["id"] == retention_id and policy["keep_last"] == 10 for policy in policies)
               for policies in received.values())
    # Removed chain references must not prevent a pending-cleanup agent receiving deletion.
    chain.steps = [{**step, "retention_id": None} for step in chain.steps]
    db.session.commit()
    received.clear()
    assert client.delete(f"/api/retentions/{retention_id}").status_code == 200
    assert set(received) == expected
    assert all(not any(policy["id"] == retention_id for policy in policies) for policies in received.values())


def test_slow_agent_dispatch_does_not_block_other_schedule_admission(setup, monkeypatch):
    app, chain, jobs, _ = setup
    blocked_agent_id = jobs[0].agent_id
    healthy_job_id = jobs[1].id
    slow_step, healthy_step = deepcopy(chain.steps)
    chain.schedules = []
    chain.steps = [slow_step]
    second_slow = BackupChain(user_id=chain.user_id, name="Other slow backup", schedules=[],
                              start_timeout_minutes=1, steps=[slow_step])
    healthy = BackupChain(user_id=chain.user_id, name="Healthy backup", schedules=[{"enabled": True, "cron_string": "1 2 * * *"}],
                         start_timeout_minutes=1, steps=[healthy_step])
    db.session.add_all([second_slow, healthy])
    db.session.commit()
    healthy_id = healthy.id
    start_chain(chain, now=NOW)
    start_chain(second_slow, now=NOW)
    both_blocked, release, healthy_started = Event(), Event(), Event()
    lock = Lock()
    waiting = 0

    def sync(agent, **kwargs):
        nonlocal waiting
        if agent.id == blocked_agent_id:
            with lock:
                waiting += 1
                if waiting == 2:
                    both_blocked.set()
            assert release.wait(10)
        return {"state": AgentOperationState.success}

    def run_job(agent, job_id, repository_id, **kwargs):
        if job_id == healthy_job_id:
            healthy_started.set()
        return {"state": AgentOperationState.success}

    monkeypatch.setattr(AgentService, "sync", sync)
    monkeypatch.setattr(AgentService, "run_job", run_job)
    pending = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        try:
            submit_pending_runs(app, executor, pending)
            assert both_blocked.wait(5)
            tick(now=NOW + timedelta(minutes=1))
            assert BackupChainRun.query.filter_by(chain_id=healthy_id).count() == 1
            assert not release.is_set()
            submit_pending_runs(app, executor, pending)
            assert healthy_started.wait(5)
            assert waiting == 2, "polling must not enqueue the blocked runs again"
            assert len(pending) <= 4
        finally:
            release.set()
        for future in pending.values():
            future.result(timeout=10)


def test_background_dispatch_has_no_unbounded_executor_queue(setup):
    app, chain, _, _ = setup
    for index in range(6):
        other = BackupChain(user_id=chain.user_id, name=f"Chain {index}", schedules=[],
                            start_timeout_minutes=1, steps=deepcopy(chain.steps))
        db.session.add(other)
        db.session.commit()
        start_chain(other, now=NOW)
    submitted = []

    class BusyExecutor:
        def submit(self, target, app, run_id):
            submitted.append(run_id)
            return Future()  # Simulate occupied workers without starting threads.

    pending = {}
    executor = BusyExecutor()
    submit_pending_runs(app, executor, pending)
    submit_pending_runs(app, executor, pending)
    assert len(submitted) == len(set(submitted)) == len(pending) == 4
    tick(now=NOW)  # Admission still works when all execution slots are occupied.
    assert BackupChainRun.query.filter_by(chain_id=chain.id, planned_slot=NOW).count() == 1
    submit_pending_runs(app, executor, pending)
    assert len(submitted) == 4
