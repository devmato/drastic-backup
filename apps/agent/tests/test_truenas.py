import configparser
import json
import shutil
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import dataset
import pytest

import drastic_agent.jobs.base as base_module
import drastic_agent.jobs.truenas_backup as backup_module
import drastic_agent.runtime.agent as agent_module
import drastic_agent.truenas as truenas_module
from drastic_agent.agent.enums import AgentOperationState
from drastic_agent.agent.report import AgentReport
from drastic_agent.jobs.file_backup import FileBackupJobHandler
from drastic_agent.jobs.truenas_backup import TrueNASBackupJobHandler, cleanup_snapshots
from drastic_agent.runtime.agent import Agent, _encode_config_secret
from drastic_agent.truenas import TrueNASClient, TrueNASError
from drastic_common.restic import ResticApi
from drastic_common.restic.exceptions import ResticCancelledError
from drastic_common.restic.repository import ResticRepository
from drastic_common.secret_envelope import encrypt_for_public_key, generate_agent_keypair
from drastic_common.truenas import TrueNASBackupConfigSchema

SETTINGS = {"api_url": "https://nas", "username": "backup", "host_root": "/mnt/host", "verify_tls": True}


def roots(names):
    return [{"dataset": name, "path": ".", "group": "dataset"} for name in names]


@pytest.fixture
def state(monkeypatch, tmp_path):
    db = dataset.connect(f"sqlite:///{tmp_path}/agent.db")
    monkeypatch.setattr(agent_module, "agent_settings", db["agent"])
    monkeypatch.setattr(agent_module, "truenas_snapshots", db["snapshots"])
    monkeypatch.setattr(backup_module, "truenas_snapshots", db["snapshots"])
    yield db
    db.engine.dispose()


def test_settings_are_encrypted_and_endpoint_changes_require_new_key(state):
    private, public = generate_agent_keypair()
    agent = Agent.__new__(Agent)
    agent._Agent__config = configparser.ConfigParser()
    agent._Agent__config["AGENT"] = {"private_key": _encode_config_secret(private), "public_key": _encode_config_secret(public)}
    report = agent.cmd_truenas_settings("save", SETTINGS, encrypt_for_public_key("api-secret", public))
    assert report.state == AgentOperationState.success
    assert "api-secret" not in json.dumps(report.data)
    assert "api-secret" not in state["agent"].find_one(name="truenas")["settings"]
    assert agent.get_truenas_client().api_key == "api-secret"
    assert agent.cmd_truenas_settings("save", {**SETTINGS, "verify_tls": False}).state == AgentOperationState.success
    assert agent.cmd_truenas_settings("save", {**SETTINGS, "api_url": "https://other"}).state == AgentOperationState.failed
    assert agent.get_truenas_client().settings["api_url"] == "https://nas"
    assert agent.cmd_delete_connection("truenas").state == AgentOperationState.success
    assert not agent.get_truenas_client().public_settings["configured"]


def test_websocket_auth_ids_errors_tls_and_secret_redaction(monkeypatch):
    sent = []
    responses = iter([
        {"jsonrpc": "2.0", "method": "notification"},
        {"id": 1, "result": {"response_type": "SUCCESS"}},
        {"id": 2, "error": {"data": {"reason": "denied api-secret"}}},
    ])
    closed = []
    def connect(url, **kwargs):
        assert url == "wss://nas/api/current"
        assert kwargs["redirect_limit"] == 0
        assert kwargs["sslopt"]["cert_reqs"] == truenas_module.ssl.CERT_REQUIRED
        return SimpleNamespace(send=lambda msg: sent.append(json.loads(msg)), settimeout=lambda _: None,
                               recv=lambda: json.dumps(next(responses)), close=lambda: closed.append(True))
    monkeypatch.setattr(truenas_module.websocket, "create_connection", connect)
    with pytest.raises(TrueNASError, match="denied <redacted>"):
        TrueNASClient(SETTINGS, "api-secret").call("pool.dataset.query")
    assert sent[0]["params"][0]["mechanism"] == "API_KEY_PLAIN"
    assert sent[1]["method"] == "pool.dataset.query"
    assert closed


@pytest.fixture
def nas(state, tmp_path, monkeypatch):
    root = tmp_path / "host"
    live = root / "mnt/tank/data"
    live.mkdir(parents=True)
    (live / "example.txt").write_text("snapshot content")
    snapshots = {}
    api = TrueNASClient({**SETTINGS, "host_root": str(root)}, "api-secret")
    controls = {"fail_delete": False, "cancel": None, "missing_mount": False, "datasets": ["tank/data"]}
    controls["connections"] = []
    calls = []

    def call(method, *params):
        calls.append((method, params))
        if method == "system.version":
            return "TrueNAS-25.10.4"
        if method == "system.host_id":
            return "nas-id"
        if method == "pool.dataset.query":
            assert params == ([], {"extra": {"flat": True, "properties": ["mountpoint", "encryption", "logicalreferenced"]}})
            return [{"id": name, "type": "FILESYSTEM", "mountpoint": f"/mnt/{name}", "locked": False,
                     "logicalreferenced": controls.get("sizes", {}).get(name)} for name in controls["datasets"]]
        if method == "pool.snapshot.create":
            config = params[0]
            source = root / "mnt" / config["dataset"]
            path = source / ".zfs/snapshot" / config["name"]
            path.mkdir(parents=True)
            shutil.copyfile(source / "example.txt", path / "example.txt")
            (source / "example.txt").write_text("changed live data")
            snapshots[f"{config['dataset']}@{config['name']}"] = {"properties": {k: {"value": v} for k, v in config["properties"].items()}}
            if controls["cancel"]:
                controls["cancel"].set()
            return {"id": f"{config['dataset']}@{config['name']}"}
        if method == "filesystem.listdir":
            return {}
        if method == "pool.snapshot.query":
            snapshot = snapshots.get(params[0][0][2])
            return [snapshot] if snapshot else []
        if method == "pool.snapshot.delete":
            if controls["fail_delete"]:
                raise TrueNASError("NAS unavailable")
            snapshots.pop(params[0])
            return True
        raise AssertionError(method)

    def source(path):
        relative = str(Path(path).relative_to(root / "mnt"))
        if "/.zfs/snapshot/" not in relative:
            return relative
        return None if controls["missing_mount"] else relative.replace("/.zfs/snapshot/", "@")

    def connect(*_args, **_kwargs):
        session = {"requests": [], "closed": False}
        controls["connections"].append(session)

        def receive():
            request = session["requests"][-1]
            response = {"id": request["id"]}
            try:
                response["result"] = ({"response_type": "SUCCESS"} if request["method"] == "auth.login_ex"
                                      else call(request["method"], *request["params"]))
            except TrueNASError as exc:
                response["error"] = {"data": {"reason": str(exc)}}
            return json.dumps(response)

        return SimpleNamespace(send=lambda msg: session["requests"].append(json.loads(msg)),
                               settimeout=lambda _: None, recv=receive,
                               close=lambda: session.update(closed=True))

    monkeypatch.setattr(truenas_module.websocket, "create_connection", connect)
    monkeypatch.setattr(truenas_module, "mount_source", source)
    return api, controls, calls, snapshots, live


def make_handler(api, restic):
    agent = SimpleNamespace(identifier="agent-1", get_truenas_client=lambda: api, resticapi=restic)
    handler = TrueNASBackupJobHandler(agent, {"id": 12, "uuid": "job-12", "config": {"paths": roots(["tank/data"])}}, 1)
    artifacts = []
    handler.start_artifact = lambda key, data, report=None: artifacts.append({"uuid": "artifact", "artifact_key": key, "data": data}) or artifacts[-1]
    handler.finish_artifact = lambda artifact, **kwargs: artifact.update(kwargs)
    return handler, artifacts


def test_snapshot_backup_restores_original_files_and_reuses_parent(nas, state, tmp_path, monkeypatch):
    binary = shutil.which("restic")
    if not binary:
        pytest.skip("restic is required (scripts/ci/install-restic.sh)")
    api, _, calls, snapshots, live = nas
    monkeypatch.chdir(tmp_path)
    restic = ResticApi(binary, ResticRepository(location="repo", password="test-password", backup_host="old-container"))
    restic.init()
    handler, artifacts = make_handler(api, restic)
    handler.run_backup(AgentReport.command_report())
    first = artifacts[0]["snapshot_id"]
    assert first and not snapshots and state["snapshots"].count() == 0
    assert (live / "example.txt").read_text() == "changed live data"
    target = tmp_path / "restore"
    restic.restore(first, str(target), include_paths=["/"])
    assert (target / "example.txt").read_text() == "snapshot content"
    restic.repository.backup_host = "drastic-550e8400-e29b-41d4-a716-446655440000"
    restic.repository.env["RESTIC_HOST"] = restic.repository.backup_host
    handler.run_backup(AgentReport.command_report())
    saved = sorted(restic.snapshots(), key=lambda item: item["time"])
    assert saved[-1]["parent"].startswith(first)
    assert saved[0]["hostname"] == "old-container"
    assert saved[-1]["hostname"] == restic.repository.backup_host
    assert len([call for call in calls if call[0] == "pool.snapshot.create"]) == 2


@pytest.mark.parametrize("cancel", [False, True])
def test_missing_mount_or_cancellation_never_backs_up_live_data_and_cleans_up(nas, state, cancel):
    api, controls, _, snapshots, _ = nas
    report = AgentReport.command_report()
    controls["cancel"] = report.cancel_event if cancel else None
    controls["missing_mount"] = not cancel
    handler, artifacts = make_handler(api, SimpleNamespace())
    with pytest.raises(ResticCancelledError if cancel else TrueNASError):
        handler.run_backup(report)
    assert not snapshots and not artifacts and not state["snapshots"].count()
    assert report.data["backup_data_complete"] is False


def test_cleanup_survives_failure_checks_ownership_and_recovers(nas, state, monkeypatch):
    api, controls, _, snapshots, _ = nas
    controls["missing_mount"] = controls["fail_delete"] = True
    handler, _ = make_handler(api, SimpleNamespace())
    with pytest.raises(TrueNASError):
        handler.run_backup(AgentReport.command_report())
    assert state["snapshots"].count() == 1
    controls["fail_delete"] = False
    snapshot = next(iter(snapshots.values()))
    snapshot["properties"]["org.drastic:agent"]["value"] = "another-agent"
    cleanup_snapshots(api, "agent-1")
    assert snapshots and state["snapshots"].count() == 1
    snapshot["properties"]["org.drastic:agent"]["value"] = "agent-1"
    restarted_db = dataset.connect(str(state.engine.url))
    with monkeypatch.context() as patch:
        patch.setattr(backup_module, "truenas_snapshots", restarted_db["snapshots"])
        backup_module.recover_snapshots(SimpleNamespace(identifier="agent-1", get_truenas_client=lambda: api))
    restarted_db.engine.dispose()
    assert not snapshots and not state["snapshots"].count()


def test_partial_dataset_failure_keeps_successful_artifact_and_cleans_all_snapshots(nas, state):
    api, controls, _, snapshots, live = nas
    other = live.parent / "other"
    other.mkdir()
    (other / "example.txt").write_text("other content")
    controls["datasets"].append("tank/other")

    def backup(**kwargs):
        if "/data/" in kwargs["cwd"]:
            raise OSError("injected read failure")
        return {"snapshot_id": "successful-dataset"}

    restic = SimpleNamespace(operation_cancellation=lambda _: nullcontext(), snapshots=lambda **_: [], backup=backup)
    handler, artifacts = make_handler(api, restic)
    handler.job["config"]["paths"] = roots(controls["datasets"])
    report = AgentReport.command_report()
    with pytest.raises(TrueNASError, match="tank/data"):
        handler.run_backup(report)
    errors = [log for log in report.logs if log["level"].name == "error"]
    assert [log["message"] for log in errors] == ["Dataset tank/data failed: injected read failure"]
    assert report.final_state == AgentOperationState.failed
    assert report.data["partial_failure"] is True
    assert artifacts[0]["state"] == AgentOperationState.failed
    assert artifacts[1]["snapshot_id"] == "successful-dataset"
    assert not snapshots and state["snapshots"].count() == 0


@pytest.mark.parametrize("select_pool", [False, True])
def test_child_selection_expands_at_run_time_without_duplicates(nas, state, select_pool):
    api, controls, calls, snapshots, _ = nas
    children = ["tank/data/photos", "tank/data/photos/new", "tank/database"]
    for name in children:
        path = api.local_path(f"/mnt/{name}")
        path.mkdir(parents=True)
        (path / "example.txt").write_text(name)
    (api.local_path("/mnt/tank") / "example.txt").write_text("pool root content")
    # Discovery happens at run time, not when the job configuration is saved.
    restic = SimpleNamespace(operation_cancellation=lambda _: nullcontext(), snapshots=lambda **_: [],
                             backup=lambda **_: {"snapshot_id": "saved"})
    handler, artifacts = make_handler(api, restic)
    handler.job["config"]["paths"] = roots(["tank"] if select_pool else ["tank/data/photos", "tank/data"])
    controls["datasets"].extend([*reversed(children), "tank"])
    report = AgentReport.command_report()
    handler.run_backup(report)
    expected = ["tank/data", "tank/data/photos", "tank/data/photos/new"]
    if select_pool:
        expected = sorted(controls["datasets"])
    created = [params[0]["dataset"] for method, params in calls if method == "pool.snapshot.create"]
    assert created == expected
    assert [artifact["data"]["dataset"] for artifact in artifacts] == expected
    assert report.data["backup_items_total"] == report.data["truenas_progress"]["datasets_total"] == len(expected)
    assert not snapshots and state["snapshots"].count() == 0


def test_backup_reuses_sessions_for_six_pending_snapshots_and_closes_before_restic(nas, state):
    api, controls, _, snapshots, _ = nas
    for index in range(5):
        name = f"tank/data/child{index}"
        path = api.local_path(f"/mnt/{name}")
        path.mkdir()
        (path / "example.txt").write_text(name)
        controls["datasets"].append(name)

    def backup(**_kwargs):
        assert all(connection["closed"] for connection in controls["connections"])
        return {"snapshot_id": "saved"}

    handler, _ = make_handler(api, SimpleNamespace(
        operation_cancellation=lambda _: nullcontext(), snapshots=lambda **_: [], backup=backup))
    controls["fail_delete"] = True
    handler.run_backup(AgentReport.command_report())
    assert len(snapshots) == state["snapshots"].count() == 6

    controls["connections"].clear()
    controls["fail_delete"] = False
    handler.run_backup(AgentReport.command_report())
    assert not snapshots and state["snapshots"].count() == 0
    connections = controls["connections"]
    assert len(connections) == 2  # Preparation (including pending cleanup), then final cleanup.
    for connection in connections:
        assert connection["closed"]
        requests = connection["requests"]
        assert sum(request["method"] == "auth.login_ex" for request in requests) == 1
        assert len({request["id"] for request in requests}) == len(requests)


@pytest.mark.parametrize("missing_parent", [False, True])
def test_recursive_selection_rejects_unavailable_sources_before_snapshots(nas, state, monkeypatch, missing_parent):
    api, _, calls, snapshots, _ = nas
    datasets = api.datasets()
    datasets.append({"id": "tank/data/locked", "available": False, "error": "Dataset is locked"})
    monkeypatch.setattr(api, "datasets", lambda: datasets)
    handler, artifacts = make_handler(api, SimpleNamespace())
    if missing_parent:
        handler.job["config"]["paths"] = roots(["tank/missing"])
    message = "tank/missing: not found" if missing_parent else "tank/data/locked: Dataset is locked"
    with pytest.raises(TrueNASError, match=message):
        handler.run_backup(AgentReport.command_report())
    assert not any(method == "pool.snapshot.create" for method, _ in calls)
    assert not artifacts and not snapshots and state["snapshots"].count() == 0


def test_dataset_exclusions_skip_whole_subtrees_before_validation_and_snapshots(nas, state, monkeypatch):
    api, controls, calls, snapshots, _ = nas
    names = ["tank", "tank/data/photos", "tank/data/photos/new", "tank/database", "tank/locked"]
    for name in names:
        path = api.local_path(f"/mnt/{name}")
        path.mkdir(parents=True, exist_ok=True)
        (path / "example.txt").write_text(name)
    controls["datasets"].extend(names)
    discover = api.datasets

    def datasets():
        return [{**item, "available": False, "error": "Dataset is locked"} if item["id"] == "tank/locked"
                else item for item in discover()]

    monkeypatch.setattr(api, "datasets", datasets)
    handler, artifacts = make_handler(api, SimpleNamespace(
        operation_cancellation=lambda _: nullcontext(), snapshots=lambda **_: [], backup=lambda **_: {"snapshot_id": "saved"}))
    handler.job["config"] = {"paths": roots(["tank", "tank/data/photos"]),
                             "exclude_paths": roots(["tank/data", "tank/locked", "tank/missing"])}
    handler.run_backup(AgentReport.command_report())
    expected = ["tank", "tank/data/photos", "tank/data/photos/new", "tank/database"]
    assert [params[0]["dataset"] for method, params in calls if method == "pool.snapshot.create"] == expected
    assert [artifact["data"]["dataset"] for artifact in artifacts] == expected
    assert not snapshots and state["snapshots"].count() == 0


@pytest.mark.parametrize("backup_type", ["truenas", "file"])
def test_selected_snapshot_paths_restore_with_literal_exclusions(nas, state, tmp_path, monkeypatch, backup_type):
    binary = shutil.which("restic")
    if not binary:
        pytest.skip("restic is required (scripts/ci/install-restic.sh)")
    api, _, _, snapshots, live = nas
    call = api.call

    def contents(root):
        for name in ["docs/keep.txt", "docs/private/secret.txt", "docs/private/keep[1]/nested/saved.txt", "docs/private/keep[1]/nested/skip.tmp",
                     "docs/private/keep[1]/cache/skip.txt", "docs/[draft]*?.txt", "docs/draftABC.txt", "other.txt"]:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(name)

    def snapshot_contents(method, *params):
        result = call(method, *params)
        if method == "pool.snapshot.create":
            root = api.local_path("/mnt/tank/data") / ".zfs/snapshot" / params[0]["name"]
            contents(root)
        return result

    monkeypatch.setattr(api, "call", snapshot_contents)
    restic = ResticApi(binary, ResticRepository(location=str(tmp_path / "repo"), password="test-password"))
    restic.init()
    handler, artifacts = make_handler(api, restic)
    def entry(path, group):
        return {"dataset": "tank/data", "path": path, "group": group}
    handler.job["config"] = {
        "paths": [entry("docs", "folder"), entry("docs/private/keep[1]", "folder"), entry("example.txt", "file")],
        "exclude_paths": [entry(".", "dataset"), entry("docs/private", "folder"), entry("docs/private/keep[1]/cache", "folder"), entry("docs/[draft]*?.txt", "file")],
        "exclude_patterns": ["**/*.tmp"],
    }
    if backup_type == "file":
        contents(live)
        config = handler.job["config"]
        agent = handler.agent
        handler = FileBackupJobHandler(agent, {"id": 12, "uuid": "job-12", "config": {
            "paths": [{"path": str(live / value["path"]), "group": value["group"]} for value in config["paths"]],
            "exclude_patterns": [{"path": str(live / value["path"]), "group": "folder" if value["group"] == "dataset" else value["group"]} for value in config["exclude_paths"]]
            + [{"path": pattern, "group": "pattern"} for pattern in config["exclude_patterns"]],
        }}, 1)
        handler.operation = {"id": 1, "uuid": "operation-1"}
        handler.start_artifact = lambda key, report=None: artifacts.append({"uuid": "artifact", "artifact_key": key}) or artifacts[-1]
        handler.finish_artifact = lambda artifact, **kwargs: artifact.update(kwargs)
    handler.run_backup(AgentReport.command_report())
    target = tmp_path / "restored"
    restic.restore(artifacts[0]["snapshot_id"], str(target), include_paths=["/"])
    if backup_type == "file":
        target = target.joinpath(*live.resolve().parts[1:])
    assert {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()} == {
        "docs/keep.txt", "docs/draftABC.txt", "docs/private/keep[1]/nested/saved.txt", "example.txt",
    }
    assert (target / "example.txt").read_text() == "snapshot content"
    assert not snapshots and not state["snapshots"].count()


def test_folder_selection_projects_to_child_dataset_mounts():
    names = {"tank/data": "/mnt/tank/data", "tank/data/child": "/mnt/tank/data/photos/child",
             "tank/data/other": "/mnt/tank/data/other", "tank/data/child/nested": "/mnt/tank/data/photos/child/nested"}
    available = {name: {"id": name, "mountpoint": mount, "available": True} for name, mount in names.items()}
    config = TrueNASBackupConfigSchema().load({
        "paths": [{"dataset": "tank/data", "path": "photos", "group": "folder"}],
        "exclude_paths": [{"dataset": "tank/data", "path": "photos/child/nested", "group": "folder"}],
    })
    selected = backup_module.backup_selection(config, available)
    assert [(dataset["id"], paths, excluded) for dataset, paths, excluded in selected] == [
        ("tank/data", ["photos"], ["photos/child/nested"]),
        ("tank/data/child", ["."], ["nested"]),
    ]
    config["paths"].append({"dataset": "tank/data/child/nested", "path": ".", "group": "dataset"})
    selected = backup_module.backup_selection(config, available)
    assert [(dataset["id"], paths) for dataset, paths, _ in selected][-1] == ("tank/data/child/nested", ["."])


@pytest.mark.parametrize("relative", ["missing.txt", "escape/example.txt"])
def test_invalid_snapshot_path_fails_and_cleans_up(nas, state, monkeypatch, relative):
    api, _, _, snapshots, live = nas
    call = api.call

    def snapshot_contents(method, *params):
        result = call(method, *params)
        if method == "pool.snapshot.create":
            root = live / ".zfs/snapshot" / params[0]["name"]
            (root / "escape").symlink_to(live, target_is_directory=True)
        return result

    monkeypatch.setattr(api, "call", snapshot_contents)
    handler, artifacts = make_handler(api, SimpleNamespace())
    handler.job["config"] = {"paths": [{"dataset": "tank/data", "path": relative, "group": "file"}]}
    with pytest.raises(TrueNASError, match="Backup failed"):
        handler.run_backup(AgentReport.command_report())
    assert artifacts[0]["state"] == AgentOperationState.failed
    assert not snapshots and not state["snapshots"].count()


@pytest.mark.parametrize("failure", [None, "before_scan", "after_summary"])
@pytest.mark.parametrize("estimated", [False, True])
def test_dataset_progress_keeps_final_summaries_and_publishes_artifacts(nas, state, monkeypatch, failure, estimated):
    api, controls, calls, snapshots, live = nas
    other = live.parent / "other"
    other.mkdir()
    (other / "example.txt").write_text("other content")
    controls["datasets"].append("tank/other")
    if estimated:
        controls["sizes"] = {"tank/data": {"parsed": 900}, "tank/other": {"rawvalue": "20"}}
    report = AgentReport.command_report()
    monkeypatch.setattr(base_module, "agent_operation_artifacts", state["artifacts"])

    def backup(**kwargs):
        first = "/data/" in kwargs["cwd"]
        size = 1000 if first else 10
        assert report.artifacts[-1]["state"] == "running"
        assert report.data["backup_progress"]["total_known"] is False
        expected_total = (920 if first or failure == "before_scan" else 1020) if estimated else None
        assert report.data["backup_bytes_total"] == expected_total
        assert report.data["backup_bytes_total_estimated"] is True
        assert report.data["backup_data_complete"] is False
        assert report.data["bytes_processed"] == (0 if first else 5 if failure == "before_scan" else 1000)
        assert kwargs["callback"].__self__ is report
        callback = kwargs["callback"]
        callback(None, pid=123)
        assert report.data["pid"] == 123
        callback({"message_type": "status", "bytes_done": 5})
        if first and failure == "before_scan":
            raise OSError("read failed")
        callback({"message_type": "verbose_status", "action": "scan_finished", "data_size": size, "total_files": 1})
        if first:
            assert report.data["backup_bytes_total"] == (1020 if estimated else None)
        summary = {"message_type": "summary", "snapshot_id": str(size), "total_bytes_processed": size, "total_files_processed": 1}
        # First summary arrives twice; second only as the return value.
        if first:
            callback(summary)
            if failure == "after_summary":
                raise OSError("partial backup after summary")
        callback(None, pid=None)
        assert "pid" not in report.data
        return summary

    restic = SimpleNamespace(operation_cancellation=lambda _: nullcontext(), snapshots=lambda **_: [], backup=backup)
    handler, _ = make_handler(api, restic)
    del handler.start_artifact, handler.finish_artifact
    handler.operation = {"id": 1}
    handler.job["config"]["paths"] = roots(controls["datasets"])
    with pytest.raises(TrueNASError, match="tank/data") if failure else nullcontext():
        handler.run_backup(report)
    assert report.data["bytes_processed"] == (15 if failure == "before_scan" else 1010)
    assert report.data["bytes_total"] == (None if failure == "before_scan" else 1010)
    assert report.data["backup_bytes_total"] == ((910 if estimated else None) if failure == "before_scan" else 1010)
    assert report.data["backup_bytes_total_estimated"] is (failure == "before_scan")
    assert report.data["backup_data_complete"] is (failure is None)
    assert report.data["current_files"] == []
    assert report.data["truenas_progress"]["phase"] == "cleanup"
    assert len(report.artifacts) == 2
    assert report.artifacts[0]["state"] == ("failed" if failure else "success")
    assert not snapshots
    assert len(calls) == 12  # Same API calls as before progress estimates; no size-query roundtrips.


@pytest.mark.parametrize("value, expected", [
    ({"parsed": 123}, 123), ({"rawvalue": "0"}, 0), ({"parsed": None, "rawvalue": "42"}, 42),
    (None, None), ({"parsed": -1}, None), ({"rawvalue": "invalid"}, None),
    ({"parsed": True}, None), ({"parsed": 1.5}, None), ("invalid", None),
])
def test_dataset_size_metadata_is_optional_and_locked_sources_stay_unavailable(nas, monkeypatch, value, expected):
    api, controls, _, _, _ = nas
    controls["sizes"] = {"tank/data": value}
    assert api.datasets()[0]["bytes_estimated"] == expected
    call = api.call
    def locked_call(method, *params):
        result = call(method, *params)
        if method == "pool.dataset.query":
            result[0]["locked"] = True
        return result
    monkeypatch.setattr(api, "call", locked_call)
    assert api.datasets()[0]["available"] is False
    assert api.datasets()[0]["error"] == "Dataset is locked"


def test_mount_detection_requires_exact_dataset_and_unescapes_paths(monkeypatch, tmp_path):
    mount = tmp_path / "data space"
    mount.mkdir()
    escaped = str(mount).replace(" ", r"\040")
    monkeypatch.setattr(Path, "read_text", lambda _self: f"101 1 0:42 / {escaped} ro - zfs tank/data@snap ro\n")
    assert truenas_module.mount_source(mount) == "tank/data@snap"
    assert truenas_module.mount_source(mount / "missing") is None


def test_uncertain_snapshot_creation_retains_intent_until_reconciliation(nas, state):
    api, _, _, _, _ = nas
    record = {"snapshot_id": "tank/data@drastic-unknown", "api_url": SETTINGS["api_url"], "host_id": "nas-id",
              "operation_uuid": "unknown", "confirmed": False, "created_at": backup_module.time()}
    record["id"] = state["snapshots"].insert(record)
    cleanup_snapshots(api, "agent-1")
    assert state["snapshots"].count() == 1
    record["created_at"] -= 301
    state["snapshots"].update(record, ["id"])
    cleanup_snapshots(api, "agent-1")
    assert state["snapshots"].count() == 0
