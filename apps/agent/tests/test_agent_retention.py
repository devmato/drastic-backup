"""Retention selects owned snapshot groups without a historical report catalog."""

import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID

import dataset
import pytest

import drastic_agent.runtime.agent as runtime_module
from drastic_agent.agent.enums import AgentReportState
from drastic_agent.runtime.agent import Agent
from drastic_agent.services.retention import RetentionService, keep_operation_ids
from drastic_common.restic.exceptions import ResticError, ResticFailedError
from drastic_common.restic.snapshots import RUN_COMPLETE_TAG

A, B, OWNER, FOREIGN = (str(UUID(int=i)) for i in (1, 2, 3, 4))


def test_retention_uses_utc_buckets_and_combines_count_and_calendar_rules():
    runs = [
        {"id": "new", "started": "2026-02-01T02:30:00+02:00"},
        {"id": "january", "started": "2026-02-01T01:30:00+02:00"},
        {"id": "december", "started": "2025-12-31T12:00:00+00:00"},
    ]
    assert keep_operation_ids(runs, {"keep_monthly": 2}) == {"new", "january"}
    assert keep_operation_ids(runs, {"keep_last": 1, "keep_yearly": 2}) == {"new", "december"}


class Backend:
    def __init__(self, job_uuids=None, default=None, owner_uuid=OWNER):
        self.owner_uuid = owner_uuid
        self.job_uuids = [B] if job_uuids is None else job_uuids
        self.default = {"keep_last": 1} if default is None else default
        self.running = []
        self.check_token = None

    def accept(self, report):
        if report.data.get("retention_check_failed"):
            self.check_token = report.uuid
        elif report.data.get("retention_check_full") and report.data.get("retention_check_verified") == self.check_token:
            self.check_token = None

    def send_report(self, report):
        self.accept(report)
        return True

    def __call__(self):
        return deepcopy({"owner_uuid": self.owner_uuid, "job_uuids": self.job_uuids, "default": self.default,
                         "check_token": self.check_token, "running_operation_uuids": self.running})


class Repo:
    def __init__(self):
        self.items, self.removed, self.checks = {}, [], []
        self.fail_forget = self.fail_prune = False
        self.prune_calls = 0
        self.on_check = None

    def add_run(self, backend, job=A, number=10, count=2, complete=True):
        operation = str(UUID(int=number))
        artifacts = []
        for index in range(count):
            identity = hashlib.sha256(f"{backend.owner_uuid}:{operation}:{index}".encode()).hexdigest()
            uuid = str(UUID(int=number * 10000 + index))
            self.items[identity] = {"id": identity,
                "time": (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=number)).isoformat(),
                "tags": [f"drastic-owner:{backend.owner_uuid}", f"job_uuid:{job}", f"operation_uuid:{operation}",
                         f"artifact_uuid:{uuid}", f"artifact_key:{index}"]}
            artifacts.append({"uuid": uuid, "snapshot_id": identity, "artifact_key": str(index)})
        if complete:
            self.items[identity]["tags"].append(RUN_COMPLETE_TAG)
        return artifacts

    def snapshots(self):
        return deepcopy(list(self.items.values()))

    def forget_snapshots(self, ids, prune=False):
        assert not prune and len(ids) <= 1000
        for identity in ids:
            self.removed.append(self.items.pop(identity))
        if self.fail_forget:
            self.fail_forget = False
            raise ResticError("Reply lost after partial removal")

    def check(self, **kwargs):
        self.checks.append(kwargs)
        if self.on_check:
            self.on_check()
        return {"errors": 0}

    def prune(self):
        self.prune_calls += 1
        if self.fail_prune:
            self.fail_prune = False
            raise ResticError("Prune interrupted")
        return {}


@pytest.fixture
def settings(tmp_path):
    database = dataset.connect(f"sqlite:///{tmp_path}/settings.db")
    table = database["agent"]
    table.create_column("name", database.types.text)
    table.create_column("settings", database.types.text)
    yield table
    database.engine.dispose()


def execute(repo, backend, settings, *, policy=None, job_uuid=B, request=None, send=None, current_operation_uuid=None):
    report = RetentionService.run(repository_id=1, job_id=None, job_uuid=job_uuid,
        current_operation_uuid=current_operation_uuid, current_policy=policy, request_context=request or backend,
        set_repository=lambda _: None, resticapi=repo, settings=settings, send_report=send or backend.send_report)
    backend.accept(report)
    return report


def test_reportless_owned_runs_are_retained_together_while_foreign_and_legacy_snapshots_stay(settings):
    repo, backend = Repo(), Backend()
    old = repo.add_run(backend, number=10)
    kept = repo.add_run(backend, number=11)
    foreign = repo.add_run(Backend(owner_uuid=FOREIGN), number=10)
    legacy = repo.add_run(backend, number=9)
    for artifact in legacy:
        repo.items[artifact["snapshot_id"]]["tags"].remove(f"drastic-owner:{OWNER}")
    # The group starts at its first snapshot, regardless of how long later components took.
    repo.items[old[-1]["snapshot_id"]]["time"] = "2027-01-01T00:00:00+00:00"
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert set(repo.items) == {a["snapshot_id"] for a in kept + foreign + legacy}
    assert repo.checks == [{"read_data": False, "read_data_subset": None}]


@pytest.mark.parametrize("policy,remaining", [(None, 4), ({"keep_last": 1}, 2), ({"keep_last": 2}, 4)])
def test_existing_jobs_use_only_the_executing_jobs_policy(settings, policy, remaining):
    repo, backend = Repo(), Backend(job_uuids=[A, B])
    repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)
    assert execute(repo, backend, settings, job_uuid=A, policy=policy).state == AgentReportState.success
    assert len(repo.items) == remaining


@pytest.mark.parametrize("response", [None, {}, {"job_uuids": []}, {"success": False}])
def test_incomplete_backend_response_never_means_all_jobs_were_deleted(settings, response):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)
    assert execute(repo, backend, settings, request=lambda: response).state == AgentReportState.warning
    assert len(repo.items) == 4 and not repo.removed


@pytest.mark.parametrize("failure", ["forget", "prune", "verification"])
def test_retry_reselects_remaining_snapshots_and_preserves_local_prune_work(settings, failure, monkeypatch):
    monkeypatch.setattr("drastic_agent.services.retention.time", lambda: 0)
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10, count=1100)
    kept = repo.add_run(backend, number=11, count=1100)
    if failure == "verification":
        backend.check_token = "known-failure"
    else:
        setattr(repo, "fail_" + failure, True)
    result = execute(repo, backend, settings, send=(lambda _: False) if failure == "verification" else None)
    assert result.state == AgentReportState.warning
    if failure == "prune":
        assert set(result.data["deleted_snapshot_ids"]) == {item["id"] for item in repo.removed}
    assert RetentionService.pending_tasks(settings) == []
    if failure == "prune":
        monkeypatch.setattr("drastic_agent.services.retention.time", lambda: 901)
        args, = RetentionService.pending_tasks(settings)
        result = RetentionService.run(**args, current_policy=None, request_context=backend,
            set_repository=lambda _: None, resticapi=repo, settings=settings, send_report=backend.send_report)
    else:
        settings.delete()  # A different agent needs no local backup history to remove remaining expired snapshots.
        result = execute(repo, backend, settings)
    assert result.state == AgentReportState.success
    assert set(repo.items) == {a["snapshot_id"] for a in kept}
    assert repo.prune_calls == (2 if failure == "prune" else 1)


@pytest.mark.parametrize("tag", ["drastic-owner:invalid", f"drastic-owner:{FOREIGN}", "job_uuid:invalid", "operation_uuid:invalid"])
def test_ambiguous_or_invalid_snapshot_identity_is_not_deleted(settings, tag):
    repo, backend = Repo(), Backend()
    old = repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)
    for artifact in old:
        repo.items[artifact["snapshot_id"]]["tags"].append(tag)
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert not repo.removed


@pytest.mark.parametrize("running", [False, True])
def test_partial_runs_cannot_expire_the_last_complete_backup(settings, running):
    repo, backend = Repo(), Backend()
    old = repo.add_run(backend, number=10)
    partial = repo.add_run(backend, number=11, count=1, complete=False)
    backend.running = [str(UUID(int=11))] if running else []
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert set(repo.items) == {a["snapshot_id"] for a in old + partial}


def test_no_confirmed_complete_run_preserves_unknown_history_until_a_full_backup_exists(settings):
    repo, backend = Repo(), Backend()
    old = repo.add_run(backend, number=10, complete=False)
    repo.add_run(backend, number=11, complete=False)
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert not repo.removed
    complete = repo.add_run(backend, number=12)
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert set(repo.items) == {item["snapshot_id"] for item in complete}
    assert {item["snapshot_id"] for item in old}.isdisjoint(repo.items)


def test_a_foreign_completion_tag_does_not_confirm_an_owned_run(settings):
    repo, backend = Repo(), Backend()
    ours = repo.add_run(backend, number=10, complete=False) + repo.add_run(backend, number=11, complete=False)
    foreign_backend = Backend(owner_uuid=FOREIGN)
    foreign = repo.add_run(foreign_backend, number=10) + repo.add_run(foreign_backend, number=11)
    # Both installations deliberately use the same job/run/artifact UUIDs.
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert not repo.removed and len(repo.items) == len(ours + foreign)
    complete = repo.add_run(backend, number=12)
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert set(repo.items) == {item["snapshot_id"] for item in complete + foreign}


@pytest.mark.parametrize("tag", ["artifact_uuid:invalid", "artifact_key:ambiguous"])
def test_ambiguous_artifact_identity_cannot_confirm_a_complete_run(settings, tag):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10, complete=False)
    newer = repo.add_run(backend, number=11)
    repo.items[newer[-1]["snapshot_id"]]["tags"].append(tag)
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert not repo.removed


def test_current_run_counts_while_another_running_run_stays_protected(settings):
    repo, backend = Repo(), Backend(job_uuids=[A])
    other = repo.add_run(backend, number=9)
    expired = repo.add_run(backend, number=10)
    current = repo.add_run(backend, number=11)
    current_uuid = str(UUID(int=11))
    backend.running = [str(UUID(int=9)), current_uuid]
    assert execute(repo, backend, settings, job_uuid=A, policy={"keep_last": 1},
        current_operation_uuid=current_uuid).state == AgentReportState.success
    assert set(repo.items) == {a["snapshot_id"] for a in current + other}
    assert {item["id"] for item in repo.removed} == {a["snapshot_id"] for a in expired}


def test_new_check_failure_cannot_be_cleared_by_an_older_check(settings):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)
    repo.on_check = lambda: setattr(backend, "check_token", "new-failure")
    assert execute(repo, backend, settings).state == AgentReportState.warning
    assert not repo.removed and backend.check_token == "new-failure"
    repo.on_check = None
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert [check["read_data"] for check in repo.checks] == [False, True]


def test_lock_conflicts_defer_cleanup_without_creating_an_integrity_block(settings, monkeypatch):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)

    def locked():
        raise ResticFailedError("repository locked", return_code=11)

    repo.on_check = locked
    assert execute(repo, backend, settings).state == AgentReportState.warning
    agent = Agent.__new__(Agent)
    agent._Agent__set_repository = lambda _: None
    agent._Agent__resticapi = repo
    monkeypatch.setattr(runtime_module, "agent_settings", settings)
    assert agent.cmd_check_repository(1).data["repository_locked"]
    assert backend.check_token is None and settings.find_one(name="retention_check_failed:1") is None
    repo.on_check = None
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert all(not check["read_data"] for check in repo.checks)


def test_new_jobs_after_initial_scan_are_not_orphans(settings):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)
    added = []

    def new_backup():
        for number in (91, 92):
            added.extend(repo.add_run(backend, job=str(UUID(int=90)), number=number))

    repo.on_check = new_backup
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert {a["snapshot_id"] for a in added}.issubset(repo.items)
