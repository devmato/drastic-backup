"""Retention selects owned snapshot groups without a historical report catalog."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import dataset
import pytest
import socketio

import drastic_agent.runtime.agent as runtime_module
from drastic_agent.agent.enums import AgentReportState
from drastic_agent.runtime.agent import Agent
from drastic_agent.services.retention import RetentionService, keep_operation_ids
from drastic_common.restic.exceptions import ResticError, ResticFailedError
from drastic_common.restic.snapshots import RUN_COMPLETE_TAG

A, B, OWNER, FOREIGN = (str(UUID(int=i)) for i in (1, 2, 3, 4))


def test_retention_uses_account_buckets_and_combines_rules():
    runs = [
        {"id": "new", "started": "2026-02-01T02:30:00+02:00"},
        {"id": "january", "started": "2026-02-01T01:30:00+02:00"},
        {"id": "december", "started": "2025-12-31T12:00:00+00:00"},
    ]
    now = datetime(2026, 2, 2, tzinfo=timezone.utc)
    policy = {"rules": [{"type": "last", "period": "month", "count": 2}]}
    assert keep_operation_ids(runs, policy, timezone_name="UTC", now=now) == {"new", "january"}
    assert keep_operation_ids(runs, policy, timezone_name="Europe/Berlin", now=now) == {"new", "december"}
    policy = {"rules": [{"type": "last", "count": 1}, {"type": "last", "period": "year", "count": 2}]}
    assert keep_operation_ids(runs, policy, timezone_name="Europe/Berlin", now=now) == {"new", "december"}


@pytest.mark.parametrize("period", [None, "hour", "day", "week", "month", "year"])
def test_count_rules_keep_latest_runs_across_gaps(period):
    runs = [{"id": "latest", "started": "2026-03-01T10:20:00Z"},
            {"id": "same", "started": "2026-03-01T10:10:00Z"},
            {"id": "old", "started": "2024-01-01T10:00:00Z"}]
    rule = {"type": "last", "count": 2, **({"period": period} if period else {})}
    assert keep_operation_ids(runs, {"rules": [rule]}, timezone_name="UTC",
        now=datetime(2026, 3, 2, tzinfo=timezone.utc)) == {"latest", "old" if period else "same"}


@pytest.mark.parametrize("rule,times,expected", [
    ({"type": "calendar", "period": "day", "count": 2},
     ["2026-03-08T02:00:00Z", "2026-03-08T01:00:00Z", "2026-03-07T01:00:00Z", "2026-03-06T01:00:00Z"], {1, 2}),
    ({"type": "calendar", "period": "week", "count": 2, "weekday": 6},
     ["2026-03-08T02:00:00Z", "2026-03-08T01:00:00Z", "2026-03-02T01:00:00Z", "2026-02-22T01:00:00Z"], {1}),
    ({"type": "calendar", "period": "month", "count": 2, "day": 1},
     ["2026-03-02T01:00:00Z", "2026-03-01T02:00:00Z", "2026-03-01T01:00:00Z", "2026-02-01T01:00:00Z", "2026-01-01T01:00:00Z"], {2, 3}),
    ({"type": "calendar", "period": "month", "count": 2, "day": 31},
     ["2026-03-01T01:00:00Z", "2026-02-28T01:00:00Z", "2026-01-31T01:00:00Z"], set()),
    ({"type": "calendar", "period": "year", "count": 2, "month": 2},
     ["2026-03-01T01:00:00Z", "2026-02-02T01:00:00Z", "2026-02-01T01:00:00Z", "2025-02-01T01:00:00Z", "2024-02-29T01:00:00Z"], {2, 3}),
])
def test_calendar_rules_keep_first_matches_and_do_not_fill_gaps(rule, times, expected):
    runs = [{"id": index, "started": value} for index, value in enumerate(times)]
    assert keep_operation_ids(runs, {"rules": [rule]}, timezone_name="Europe/Berlin",
        now=datetime(2026, 3, 8, 12, tzinfo=timezone.utc)) == expected


def test_period_rules_union_without_double_counting_or_intersection():
    runs = [{"id": "latest", "started": "2026-03-08T10:00:00Z"},
            {"id": "first", "started": "2026-03-08T01:00:00Z"},
            {"id": "last-month", "started": "2026-02-01T01:00:00Z"},
            {"id": "expired", "started": "2025-01-01T01:00:00Z"}]
    rules = [{"type": "last", "count": 1}, {"type": "calendar", "period": "day", "count": 1},
             {"type": "calendar", "period": "month", "count": 2, "day": 1}]
    assert keep_operation_ids(runs, {"rules": rules}, timezone_name="Europe/Berlin",
        now=datetime(2026, 3, 8, 12, tzinfo=timezone.utc)) == {"latest", "first", "last-month"}


def test_recent_days_follow_local_dst_and_months_clamp_to_calendar_end():
    runs = [{"id": "inside", "started": "2026-03-28T23:30:00Z"},
            {"id": "outside", "started": "2026-03-28T23:29:59Z"}]
    assert keep_operation_ids(runs, {"rules": [{"type": "recent", "days": 1}]},
        timezone_name="Europe/Berlin", now=datetime.fromisoformat("2026-03-30T00:30:00+02:00")) == {"inside"}
    runs = [{"id": "inside", "started": "2024-02-21T11:00:00Z"},
            {"id": "outside", "started": "2024-02-21T10:59:59Z"}]
    assert keep_operation_ids(runs, {"rules": [{"type": "recent", "months": 1, "weeks": 1, "days": 1}]},
        timezone_name="Europe/Berlin", now=datetime.fromisoformat("2024-03-31T12:00:00+02:00")) == {"inside"}
    assert keep_operation_ids(runs, {"rules": [{"type": "recent", "days": 365000, "weeks": 52000, "months": 12000}]},
        timezone_name="Europe/Berlin", now=datetime.fromisoformat("2024-03-31T12:00:00+02:00")) == {"inside", "outside"}


def test_hourly_thinning_distinguishes_repeated_dst_hours_and_since_uses_local_midnight():
    runs = [{"id": "second", "started": "2026-10-25T01:30:00Z"},
            {"id": "first", "started": "2026-10-25T00:30:00Z"},
            {"id": "midnight", "started": "2026-10-24T22:00:00Z"}]
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    assert keep_operation_ids(runs, {"rules": [{"type": "last", "count": 2, "period": "hour"}]},
        timezone_name="Europe/Berlin", now=now) == {"first", "second"}
    assert keep_operation_ids(runs, {"rules": [{"type": "since", "date": "2026-10-25"}]},
        timezone_name="Europe/Berlin", now=now) == {"first", "second"}


def test_weekly_rules_cross_iso_year_boundaries_in_the_account_zone():
    runs = [{"id": "sunday", "started": "2027-01-03T12:00:00Z"},
            {"id": "friday", "started": "2027-01-01T12:00:00Z"},
            {"id": "previous", "started": "2026-12-27T12:00:00Z"}]
    now = datetime(2027, 1, 3, 20, tzinfo=timezone.utc)
    for rule in ({"type": "last", "period": "week", "count": 2},
                 {"type": "calendar", "period": "week", "count": 2, "weekday": 6}):
        assert keep_operation_ids(runs, {"rules": [rule]}, timezone_name="Europe/Berlin", now=now) == {"sunday", "previous"}


class Backend:
    def __init__(self, job_uuids=None, default=None, owner_uuid=OWNER):
        self.owner_uuid = owner_uuid
        self.job_uuids = [B] if job_uuids is None else job_uuids
        self.default = {"rules": [{"type": "last", "count": 1}]} if default is None else default
        self.timezone = "UTC"
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
                          "check_token": self.check_token, "running_operation_uuids": self.running, "timezone": self.timezone})


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


def execute(repo, backend, settings, *, policy=None, job_uuid=B, request=None, send=None, current_operation_uuid=None, **options):
    report = RetentionService.run(repository_id=1, job_id=None, job_uuid=job_uuid,
        current_operation_uuid=current_operation_uuid, current_policy=policy, request_context=request or backend,
        set_repository=lambda _: None, resticapi=repo, settings=settings, send_report=send or backend.send_report, **options)
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
    result = execute(repo, backend, settings)
    assert result.state == AgentReportState.success
    assert result.source.name == "triggered"  # Internal cleanup without a parent is still not a solo start.
    assert set(repo.items) == {a["snapshot_id"] for a in kept + foreign + legacy}
    assert repo.checks == [{"read_data": False, "read_data_subset": None}]
    assert 'Removed restore point: Deleted job (00000000) — 01 Jan 2026, 00:00 UTC — 2 snapshots' in result.log


@pytest.mark.parametrize("existing,names,count,label", [
    (True, {A: "NAS daily"}, 2, '"NAS daily"'),
    (True, {A: 'NAS "täglich"\nbackup'}, 1, '"NAS \\"täglich\\"\\nbackup"'),
    (False, {}, 2, "Deleted job (00000000)"),
    (True, None, 2, "Job (00000000)"),
])
def test_removed_restore_points_log_job_name_earliest_date_and_count_in_account_zone(settings, existing, names, count, label):
    repo, backend = Repo(), Backend(job_uuids=[A] if existing else [])
    backend.timezone = "Europe/Berlin"
    old = repo.add_run(backend, number=10, count=count)
    kept = repo.add_run(backend, number=11)
    for index, artifact in enumerate(old):
        repo.items[artifact["snapshot_id"]]["time"] = f"2026-10-{20 + index * 2}T00:00:00Z"
    for artifact in kept:
        repo.items[artifact["snapshot_id"]]["time"] = "2026-10-21T00:00:00Z"

    def context():
        return {**backend(), **({"job_names": names} if names is not None else {})}

    result = execute(repo, backend, settings, job_uuid=A, policy=backend.default, request=context)
    assert result.state == AgentReportState.success
    assert f"Removed restore point: {label} — 20 Oct 2026, 02:00 CEST — {count} snapshot{'s' if count != 1 else ''}" in result.log
    assert result.log.count("Removed restore point:") == 1
    assert "Partially removed" not in result.log


@pytest.mark.parametrize("failure", [None, "first_batch", "second_batch", "prune"])
def test_removal_labels_only_count_confirmed_batches_and_survive_prune_failures(settings, monkeypatch, failure):
    repo, backend = Repo(), Backend()
    old = repo.add_run(backend, number=10, count=1100)
    repo.add_run(backend, number=11)
    calls = 0
    forget = repo.forget_snapshots

    def forget_batch(ids, prune=False):
        nonlocal calls
        calls += 1
        forget(ids, prune=prune)
        if (failure == "first_batch" and calls == 1) or (failure == "second_batch" and calls == 2):
            raise ResticError("Forget response lost")

    monkeypatch.setattr(repo, "forget_snapshots", forget_batch)
    repo.fail_prune = failure == "prune"
    result = execute(repo, backend, settings)
    assert result.state == (AgentReportState.warning if failure else AgentReportState.success)
    if failure == "first_batch":
        assert "removed restore point:" not in result.log.lower()
        assert result.data["deleted_snapshot_ids"] == []
    elif failure == "second_batch":
        assert "Partially removed restore point: Deleted job (00000000) — 01 Jan 2026, 00:00 UTC — 1000 of 1100 snapshots" in result.log
        assert "Removed restore point:" not in result.log
        assert len(result.data["deleted_snapshot_ids"]) == 1000
    else:
        assert "Removed restore point: Deleted job (00000000) — 01 Jan 2026, 00:00 UTC — 1100 snapshots" in result.log
        assert "Partially removed" not in result.log
        assert set(result.data["deleted_snapshot_ids"]) == {item["snapshot_id"] for item in old}


@pytest.mark.parametrize("policy,remaining", [(None, 4), ({"rules": [{"type": "last", "count": 1}]}, 2), ({"rules": [{"type": "last", "count": 2}]}, 4)])
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


@pytest.mark.parametrize("unavailable", [False, True])
def test_context_is_read_after_scan_and_refreshed_before_deletion(settings, monkeypatch, unavailable):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)
    events = []
    snapshots = repo.snapshots

    def scan():
        events.append("scan")
        return snapshots()

    def context():
        events.append("context")
        if unavailable and repo.checks:
            raise RuntimeError("Backend connection lost; cleanup deferred")
        return backend()

    monkeypatch.setattr(repo, "snapshots", scan)
    repo.on_check = lambda: events.append("check")
    report = execute(repo, backend, settings, request=context)
    assert events == ["scan", "context", "check", "scan", "context"]
    if unavailable:
        assert report.state == AgentReportState.warning and report.data["cleanup_pending"]
        assert not repo.removed and not repo.prune_calls
        assert "removed restore point:" not in report.log.lower()
        assert settings.find_one(name=f"retention_task:1:{B}") is not None
    else:
        assert report.state == AgentReportState.success
        assert len(repo.removed) == 2 and repo.prune_calls == 1


@pytest.mark.parametrize("failure,reason", [
    ("timeout", "backend response timed out after 3 seconds"),
    ("disconnected", "agent is not connected to the backend"),
    ("rejected", "backend rejected request (AgentException)"),
    ("transport", "backend request failed (ConnectionError)"),
    ("transport-type-error", "backend request failed (TypeError)"),
])
def test_context_transport_failures_are_explained_in_the_operation_log(settings, monkeypatch, failure, reason):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)
    calls = []

    def call(*args, **kwargs):
        assert args[1]["args"]["include_job_names"] is True
        calls.append(args)
        if failure == "timeout":
            raise socketio.exceptions.TimeoutError("private transport detail")
        if failure == "transport":
            raise socketio.exceptions.ConnectionError("private transport detail")
        if failure == "transport-type-error":
            raise TypeError("private transport detail")
        return {"success": False, "result": {"error": "AgentException"}}

    agent = Agent.__new__(Agent)
    agent.client = SimpleNamespace(connected=failure != "disconnected", call=call)
    agent._Agent__resticapi = repo
    monkeypatch.setattr(agent, "_Agent__set_repository", lambda _: None)
    monkeypatch.setattr(runtime_module, "agent_settings", settings)
    monkeypatch.setattr(runtime_module, "jobs", SimpleNamespace(find_one=lambda **kwargs: None))
    monkeypatch.setenv("DRASTIC_COMMAND_TIMEOUT_SECONDS", "3")
    report = agent.cmd_run_retention(1)
    assert report.state == AgentReportState.warning and report.data["cleanup_pending"]
    assert reason in report.log and "private transport detail" not in report.log
    assert len(calls) == (0 if failure == "disconnected" else 1)
    assert not repo.removed and not repo.prune_calls and not repo.checks


@pytest.mark.parametrize("repository_scope", [False, True])
@pytest.mark.parametrize("failure", [None, "rejected", "incomplete", "type_error"])
def test_runtime_retention_falls_back_to_previous_backend_signature_once(settings, monkeypatch, repository_scope, failure):
    repo, backend = Repo(), Backend()
    old = repo.add_run(backend, number=10)
    kept = repo.add_run(backend, number=11)
    calls = []

    def old_context(repository_id, include_policies=False):
        assert repository_id == 1
        if failure == "rejected":
            return {"success": False, "result": {"error": "AgentException"}}
        if failure == "type_error":
            raise TypeError("Backend failure")
        context = backend()
        if include_policies:
            context["job_policies"] = {B: None}
        return {"success": True, "result": {} if failure == "incomplete" else context}

    def call(event, payload, **kwargs):
        assert event == "request" and payload["action"] == "retention_context"
        calls.append(payload["args"].copy())
        try:
            return old_context(**payload["args"])
        except TypeError:
            return {"success": False, "result": {"error": "TypeError"}}

    agent = Agent.__new__(Agent)
    agent.client = SimpleNamespace(connected=True, call=call)
    agent._Agent__resticapi = repo
    agent._Agent__send_report = backend.send_report
    monkeypatch.setattr(agent, "_Agent__set_repository", lambda _: None)
    monkeypatch.setattr(runtime_module, "agent_settings", settings)
    monkeypatch.setattr(runtime_module, "jobs", SimpleNamespace(find_one=lambda **kwargs: None))
    result = agent.cmd_run_retention(1, repository_scope=repository_scope)
    args = {"repository_id": 1, **({"include_policies": True} if repository_scope else {})}
    assert calls == [{**args, "include_job_names": True}, args] + ([args] if failure is None else [])
    if failure is None:
        assert result.state == AgentReportState.success and not result.data["cleanup_pending"]
        assert set(result.data["deleted_snapshot_ids"]) == {item["snapshot_id"] for item in old}
        assert set(repo.items) == {item["snapshot_id"] for item in kept}
        assert "Removed restore point: Deleted job" in result.log and repo.prune_calls == 1
    else:
        assert result.state == AgentReportState.warning and result.data["cleanup_pending"]
        assert not repo.removed and not repo.prune_calls and not repo.checks


@pytest.mark.parametrize("failure", ["forget", "prune", "verification", "policy_removed"])
@pytest.mark.parametrize("solo", [False, True])
def test_retry_reselects_remaining_snapshots_and_preserves_local_prune_work(settings, failure, solo):
    repo, backend = Repo(), Backend()
    old = repo.add_run(backend, number=10, count=1100)
    kept = repo.add_run(backend, number=11, count=1100)
    verification = failure in {"verification", "policy_removed"}
    if verification:
        backend.check_token = "known-failure"
    else:
        setattr(repo, "fail_" + failure, True)
    def context():
        return {**backend(), "job_policies": {B: None}} if solo else backend()

    result = execute(repo, backend, settings, request=context,
        send=(lambda _: False) if verification else None,
        **({"operation_uuid": str(uuid4()), "repository_scope": True} if solo else {}))
    original_uuid = result.uuid
    assert result.state == AgentReportState.warning
    if failure == "prune":
        assert set(result.data["deleted_snapshot_ids"]) == {item["id"] for item in repo.removed}
    assert RetentionService.pending_tasks(settings, 2) == []
    if failure in {"prune", "policy_removed"}:
        args, = RetentionService.pending_tasks(settings, 1)
        assert args["current_operation_uuid"] == original_uuid and args["repository_scope"] is solo
        backend.default = None  # Policy removal must finish pending work, including already-needed prune.
        result = RetentionService.run(**args, current_policy=None, request_context=context,
            set_repository=lambda _: None, resticapi=repo, settings=settings, send_report=backend.send_report)
        assert result.parent_operation_uuid == original_uuid and result.source.name == "triggered"
        assert result.data["cleanup_pending"] is False
    else:
        settings.delete()  # A different agent needs no local backup history to remove remaining expired snapshots.
        result = execute(repo, backend, settings)
    assert result.state == AgentReportState.success
    remaining = old + kept if failure == "policy_removed" else kept
    assert set(repo.items) == {a["snapshot_id"] for a in remaining}
    assert repo.prune_calls == {"prune": 2, "policy_removed": 0}.get(failure, 1)


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
    assert execute(repo, backend, settings, job_uuid=A, policy={"rules": [{"type": "last", "count": 1}]},
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


@pytest.mark.parametrize("invalid", ["missing-zone", "invalid-zone", "empty-rules", "unknown-rule", "legacy-policy", "changed-zone", "changed-owner", "invalid-job-names", "invalid-job-name"])
def test_invalid_or_changed_context_never_deletes_backups(settings, invalid):
    repo, backend = Repo(), Backend()
    repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)

    def context():
        value = backend()
        if invalid == "missing-zone":
            value.pop("timezone")
        elif invalid == "invalid-zone":
            value["timezone"] = "not/a-zone"
        elif invalid == "empty-rules":
            value["default"] = {"rules": []}
        elif invalid == "unknown-rule":
            value["default"] = {"rules": [{"type": "unknown"}]}
        elif invalid == "legacy-policy":
            value["default"] = {"keep_last": 1}
        elif invalid == "invalid-job-names":
            value["job_names"] = []
        elif invalid == "invalid-job-name":
            value["job_names"] = {A: None}
        return value

    if invalid == "changed-zone":
        repo.on_check = lambda: setattr(backend, "timezone", "Europe/Berlin")
    elif invalid == "changed-owner":
        repo.on_check = lambda: setattr(backend, "owner_uuid", FOREIGN)
    report = execute(repo, backend, settings, request=context)
    assert report.state == AgentReportState.warning and report.data["cleanup_pending"]
    assert not repo.removed and not repo.prune_calls


@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-01-01T00:00:00"])
def test_missing_or_offsetless_snapshot_times_do_not_use_the_host_zone(settings, timestamp):
    repo, backend = Repo(), Backend()
    artifacts = repo.add_run(backend, number=10)
    repo.add_run(backend, number=11)
    repo.items[artifacts[0]["snapshot_id"]]["time"] = timestamp
    report = execute(repo, backend, settings)
    assert report.state == AgentReportState.warning and not repo.removed
    assert "invalid timestamp" in report.log


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


def test_no_policy_skips_automatic_history_but_manual_run_explains_the_noop(settings):
    repo, backend = Repo(), Backend()
    backend.default = None
    repo.add_run(backend)
    automatic = execute(repo, backend, settings, current_operation_uuid=str(UUID(int=100)))
    assert automatic.type.name == "command" and automatic.history_operation is None
    assert "no applicable policy" in automatic.log
    assert not list(settings.all()) and not repo.removed
    manual = execute(repo, backend, settings, job_uuid=None, operation_uuid=str(UUID(int=101)))
    assert manual.type.name == "retention" and manual.history_operation is not None
    assert manual.state == AgentReportState.success and not manual.data["cleanup_pending"]
    assert "no applicable policy" in manual.log


@pytest.mark.parametrize("missing_policy", [False, True])
def test_repository_scope_uses_each_jobs_policy_and_default_only_for_orphans(settings, missing_policy):
    repo, backend = Repo(), Backend(job_uuids=[A, B])
    old_a = repo.add_run(backend, job=A, number=10)
    new_a = repo.add_run(backend, job=A, number=11)
    disabled = repo.add_run(backend, job=B, number=12) + repo.add_run(backend, job=B, number=13)
    orphan = str(UUID(int=1000))
    old_orphan = repo.add_run(backend, job=orphan, number=14)
    new_orphan = repo.add_run(backend, job=orphan, number=15)

    def context():
        return {**backend(), "job_names": {A: "NAS daily", B: "VM weekly"},
                "job_policies": {A: {"rules": [{"type": "last", "count": 1}]}, **({} if missing_policy else {B: None})}}

    report = execute(repo, backend, settings, job_uuid=None, operation_uuid=str(UUID(int=102)),
        repository_scope=True, request=context)
    if missing_policy:
        assert report.state == AgentReportState.warning and not repo.removed
    else:
        assert report.state == AgentReportState.success
        assert set(repo.items) == {item["snapshot_id"] for item in new_a + disabled + new_orphan}
        assert set(report.data["deleted_snapshot_ids"]) == {item["snapshot_id"] for item in old_a + old_orphan}
        assert "4 snapshot(s) removed" in report.log
        assert report.log.count("Removed restore point:") == 2
        assert 'Removed restore point: "NAS daily" — 01 Jan 2026, 00:00 UTC — 2 snapshots' in report.log
        assert 'Removed restore point: Deleted job (00000000) — 01 Jan 2026, 00:00 UTC — 2 snapshots' in report.log


def test_existing_completion_intents_retry_under_their_backup(settings):
    operation_uuid = str(UUID(int=105))
    settings.insert({"name": f"backup_completion:1:{operation_uuid}", "settings": json.dumps({
        "operation_uuid": operation_uuid, "retry_at": 0,
        "args": {"repository_id": 1, "job_id": 2, "job_uuid": A, "current_operation_uuid": None}})})
    task, = RetentionService.pending_tasks(settings, 1)
    assert task["current_operation_uuid"] == operation_uuid
