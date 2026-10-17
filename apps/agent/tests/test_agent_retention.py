"""Whole-run retention uses acknowledged reports, never a second catalog in restic."""

import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID

import dataset
import pytest

import drastic_agent.runtime.agent as runtime_module
from drastic_agent.agent.enums import AgentReportState
from drastic_agent.agent.schemas import AgentReportSchema
from drastic_agent.runtime.agent import Agent
from drastic_agent.services.retention import RetentionService, keep_operation_ids
from drastic_common.restic.exceptions import ResticError, ResticFailedError

A, B = (str(UUID(int=i)) for i in (1, 2))


def test_retention_uses_utc_buckets_and_combines_count_and_calendar_rules():
    runs = [
        {"id": "new", "ended": "2026-02-01T02:30:00+02:00"},
        {"id": "january", "ended": "2026-02-01T01:30:00+02:00"},
        {"id": "december", "ended": "2025-12-31T12:00:00+00:00"},
    ]
    assert keep_operation_ids(runs, {"keep_monthly": 2}) == {"new", "january"}
    assert keep_operation_ids(runs, {"keep_last": 1, "keep_yearly": 2}) == {"new", "december"}


class Backend:
    """An in-memory report transport double; the real ownership/commit checks have backend tests."""
    def __init__(self, job_uuids=None, default=None):
        self.runs = {}
        self.job_uuids = [B] if job_uuids is None else job_uuids
        self.default = {"keep_last": 1} if default is None else default
        self.check_token = None

    def accept(self, report):
        if report is None:
            return
        payload = report if isinstance(report, dict) else AgentReportSchema().dump(report)
        data = payload["data"]
        if payload["type"] == "backup":
            self.runs[payload["uuid"]] = {"id": payload["uuid"], "job_uuid": data["job_uuid"],
                "ended": payload["ended"] or payload["started"],
                "finished": payload["state"] != "running",
                "artifacts": [{**a, "forgotten": False} for a in payload["artifacts"]]}
        for run in self.runs.values():
            for artifact in run["artifacts"]:
                if any(a["uuid"] == artifact["uuid"] for a in data.get("forgotten_artifacts", [])):
                    artifact["forgotten"] = True
        if data.get("retention_check_failed"):
            self.check_token = payload["uuid"]
        elif data.get("retention_check_full") and data.get("retention_check_verified") == self.check_token:
            self.check_token = None

    def __call__(self, report=None):
        self.accept(report)
        return deepcopy({"job_uuids": self.job_uuids, "default": self.default, "check_token": self.check_token,
                         "runs": [run for run in self.runs.values() if any(not a["forgotten"] for a in run["artifacts"])]})


class Repo:
    def __init__(self):
        self.items, self.removed, self.checks = {}, [], []
        self.fail_forget = self.fail_prune = False
        self.prune_calls = 0
        self.on_check = None

    def add_run(self, backend, job=A, number=10, count=2):
        operation = str(UUID(int=number))
        artifacts = []
        for index in range(count):
            identity = hashlib.sha256(f"{operation}:{index}".encode()).hexdigest()
            uuid = str(UUID(int=number * 10000 + index))
            self.items[identity] = {"id": identity, "tags": [f"job_uuid:{job}", f"operation_uuid:{operation}",
                f"artifact_uuid:{uuid}", f"artifact_key:{index}"]}
            artifacts.append({"uuid": uuid, "snapshot_id": identity, "artifact_key": str(index),
                               "forgotten": False})
        backend.runs[operation] = {"id": operation, "job_uuid": job, "artifacts": artifacts,
            "finished": True, "ended": (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=number)).isoformat()}
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


def execute(repo, backend, settings, *, policy=None, job_uuid=B, request=None, acknowledge=True):
    report = RetentionService.run(repository_id=1, job_id=None, job_uuid=job_uuid,
        current_operation_uuid=None, current_policy=policy, request_context=request or backend,
        set_repository=lambda _: None, resticapi=repo, settings=settings)
    if acknowledge:
        backend.accept(report)
    return report


def test_deleted_job_retains_whole_runs_and_preserves_unreported_snapshots(settings):
    repo, backend = Repo(), Backend()
    removed = repo.add_run(backend, number=10)
    kept = repo.add_run(backend, number=11)
    unknown = repo.add_run(Backend(), job=B, number=12)
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert set(repo.items) == {a["snapshot_id"] for a in kept + unknown}
    assert all(a["forgotten"] for a in removed)
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
    assert execute(repo, backend, settings, request=lambda _: response).state == AgentReportState.warning
    assert len(repo.items) == 4 and not repo.removed


@pytest.mark.parametrize("failure", ["forget", "prune", "result", "verification"])
def test_other_agent_finishes_interrupted_cleanup_from_reports(settings, failure):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10, count=1100)
    kept = repo.add_run(backend, number=11, count=1100)
    if failure == "verification":
        backend.check_token = "known-failure"
    if failure in {"forget", "prune"}:
        setattr(repo, "fail_" + failure, True)

    def request(report):
        response = backend(report)
        if failure == "verification" and report is not None:
            raise TimeoutError("Verification acknowledgment lost")
        return response

    result = execute(repo, backend, settings, request=request, acknowledge=failure != "result")
    assert result.state == (AgentReportState.success if failure == "result" else AgentReportState.warning)
    if failure == "verification":
        assert not repo.removed
    # The new executor has no local queue, backup history or prune marker.
    settings.delete()
    remaining = set(repo.items)
    backend.default = None
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert set(repo.items) == remaining
    backend.default = {"keep_last": 1}
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert set(repo.items) == {a["snapshot_id"] for a in kept}
    assert repo.prune_calls == (2 if failure in {"prune", "result"} else 1)


@pytest.mark.parametrize("damage", ["missing", "older_kept", "tags", "time"])
def test_no_deletion_when_a_retained_run_is_missing_or_invalid(settings, damage):
    repo, backend = Repo(), Backend()
    if damage in {"missing", "older_kept", "tags"}:
        older = repo.add_run(backend, number=9)
    only = repo.add_run(backend)
    if damage == "missing":
        repo.items.pop(only[0]["snapshot_id"])
    elif damage == "older_kept":
        backend.default = {"keep_last": 2}
        repo.items.pop(older[0]["snapshot_id"])
    elif damage == "tags":
        repo.items[only[0]["snapshot_id"]]["tags"] = []
    else:
        backend.default = {"keep_yearly": 1}
        next(iter(backend.runs.values()))["ended"] = None
    execute(repo, backend, settings)
    assert not repo.removed


@pytest.mark.parametrize("finished", [False, True])
def test_partial_runs_count_normally_but_running_backups_are_preserved(settings, finished):
    repo, backend = Repo(), Backend()
    backend.check_token = None
    old = repo.add_run(backend, number=10)
    partial = repo.add_run(backend, number=11, count=1)
    backend.runs[str(UUID(int=11))]["finished"] = finished
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert set(repo.items) == {a["snapshot_id"] for a in (partial if finished else old + partial)}
    if finished:
        assert repo.checks[0]["read_data"] is False


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
    assert not repo.removed
    repo.on_check = None
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert all(not check["read_data"] for check in repo.checks)


def test_new_jobs_after_initial_scan_are_not_orphans(settings):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)
    new_job = str(UUID(int=90))
    added = []

    def new_backup():
        added.extend(repo.add_run(backend, job=new_job, number=91))
        added.extend(repo.add_run(backend, job=new_job, number=92))

    repo.on_check = new_backup
    assert execute(repo, backend, settings).state == AgentReportState.success
    assert {a["snapshot_id"] for a in added}.issubset(repo.items)
