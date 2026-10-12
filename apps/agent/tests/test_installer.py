import argparse
import bz2
import fcntl
import importlib.util
import io
import json
import os
import plistlib
import pty
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def installer(tmp_path, monkeypatch, request):
    script = Path(__file__).resolve().parents[3] / "scripts/drastic-agent-installer.py"
    spec = importlib.util.spec_from_file_location("installer", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MACOS = getattr(request, "param", False)
    root = tmp_path / "agent"
    root.mkdir()
    (root / module.MARKER).touch()
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "WRAPPER", tmp_path / "bin/drastic-agent")
    monkeypatch.setattr(module, "SERVICE", tmp_path / "drastic-agent.service")
    if module.MACOS:
        python = root / "tools/python/bin/python3"
        python.parent.mkdir(parents=True)
        executable = python.with_name("python3.11")
        executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        executable.chmod(0o755)
        python.symlink_to(executable.name)
        monkeypatch.setattr(module, "sys", SimpleNamespace(**{**vars(sys), "executable": str(python)}))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return module


def arguments(command="install", **overrides):
    return argparse.Namespace(**{
        "command": command, "source": None, "repository": None, "ref": None, "revision": None,
        "server": "https://backup.example.net", "user": "admin", "password": "secret",
        "yes": True, "purge": False, **overrides,
    })


def mock_runtime(installer, monkeypatch, *, mock_packages=False):
    service = {"active": False, "enabled": False, "fail_start": False, "refuse_stop": False, "stop_error": False,
               "loaded": False, "restic_checks": 0, "fail_restic": False}
    real_run = installer.run
    real_kill = os.kill

    def kill(pid, sig):
        if pid != 999777:
            return real_kill(pid, sig)
        if not service["active"]:
            raise ProcessLookupError()

    if installer.MACOS:
        monkeypatch.setattr(os, "kill", kill)

    def run(*args, **kwargs):
        args = [str(arg) for arg in args]
        if installer.MACOS and args[1:2] == ["-c"] and 'controller["service_text"]' in args[2]:
            # Model a private interpreter's sys.executable while executing the target renderer.
            return real_run(sys.executable, '-c', 'import sys; sys.executable = sys.argv.pop(1); ' + args[2],
                            installer.sys.executable, *args[3:], **kwargs)
        if args[0] == "launchctl":
            action = args[1]
            if action == "print-disabled":
                return subprocess.CompletedProcess(args, 0, stdout=f'"net.drastic.agent" => {str(not service["enabled"]).lower()}')
            if action == "print":
                if not service["loaded"] or args[2].endswith(".update"):
                    return subprocess.CompletedProcess(args, 113, stdout="", stderr="Could not find service")
                state = "state = running\npid = 999777" if service["active"] else "state = not running\nlast exit code = 1"
                return subprocess.CompletedProcess(args, 0, stdout=state, stderr="")
            if action == "bootstrap":
                plist = plistlib.loads(Path(args[3]).read_bytes())
                assert plist["RunAtLoad"] and plist["KeepAlive"]
                service["loaded"] = True
                service["active"] = not service["fail_start"]
                service["fail_start"] = False
            elif action == "bootout":
                if service["stop_error"]:
                    raise subprocess.CalledProcessError(5, args)
                service["loaded"] = False
                if not service["refuse_stop"]:
                    service["active"] = False
            elif action in {"enable", "disable"}:
                service["enabled"] = action == "enable"
            return subprocess.CompletedProcess(args, 0)
        if args[0] == "systemctl":
            action = args[1]
            if action == "show":
                state = "loaded" if installer.SERVICE.exists() else "not-found"
                return subprocess.CompletedProcess(args, 0, stdout=state + "\n")
            if action == "start":
                service["active"] = not service["fail_start"]
                service["fail_start"] = False
            elif action == "stop" and not service["refuse_stop"]:
                service["active"] = False
            elif action in {"enable", "disable"}:
                service["enabled"] = action == "enable"
            code = 1 if action == "stop" and service["stop_error"] else 0
            if action in {"is-active", "is-enabled"}:
                code = 0 if service[action.removeprefix("is-")] else 3
            return subprocess.CompletedProcess(args, code)
        if args[0].endswith("tools/bin/uv"):
            venv = Path(kwargs["env"]["UV_PROJECT_ENVIRONMENT"])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin/drastic-agent").touch()
            return subprocess.CompletedProcess(args, 0)
        if args[0].endswith("venv/bin/drastic-agent"):
            if args[1] == "register":
                data = installer.ROOT / "data/config.ini"
                data.write_text("[AGENT]\nidentifier = original-id\nsecret = original-secret\n"
                                "[SERVER]\nurl = https://backup.example.net\n")
            return subprocess.CompletedProcess(args, 0)
        if (args[0].endswith("venv/bin/python") and args[1] == "-c"
                and "drastic_agent.integrations.restic_binary import ensure_binary" in args[2]):
            assert args[3] == str(installer.ROOT / "tools/restic" if installer.MACOS else installer.data_directory() / "bin")
            assert args[4] == ("darwin" if installer.MACOS else "linux")
            service["restic_checks"] += 1
            if installer.MACOS:
                folder = installer.ROOT / "tools/restic"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "restic_0.19.1_darwin_arm64").touch()
            if service["fail_restic"]:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0)
        if mock_packages and args[0] == "bash" and args[1].endswith("install-agent-dependencies.sh"):
            return subprocess.CompletedProcess(args, 0)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(installer, "run", run)
    return service


@pytest.mark.parametrize("state", ["not-found", "loaded", "error"])
def test_stop_skips_only_missing_service(installer, monkeypatch, state):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if args[1] == "show":
            assert kwargs.get("check", True)
            return subprocess.CompletedProcess(args, 0, stdout=state + "\n")
        return subprocess.CompletedProcess(args, 3 if args[1] == "is-active" else 0)

    monkeypatch.setattr(installer, "run", run)
    installer.stop()
    assert ("systemctl", "show", "--property=LoadState", "--value", installer.SERVICE.name) in calls
    assert (("systemctl", "stop", installer.SERVICE.name) in calls) == (state != "not-found")
    assert calls[-1] == ("systemctl", "is-active", "--quiet", installer.SERVICE.name)


@pytest.fixture
def repository(tmp_path, installer, monkeypatch):
    repo = tmp_path / "repository"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "version").write_text("one")
    (repo / "scripts").mkdir()
    (repo / "scripts/drastic-agent-installer.py").write_text("# test lifecycle manager\nMACOS_LIFECYCLE = True\n")
    restic_module = repo / "apps/agent/src/drastic_agent/integrations/restic_binary.py"
    restic_module.parent.mkdir(parents=True)
    restic_module.touch()
    version_script = Path(__file__).resolve().parents[3] / "libs/python/common/src/drastic_common/version.py"
    target = repo / "libs/python/common/src/drastic_common/version.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(version_script.read_bytes())
    (repo / ".gitignore").write_text("build-version.txt\nbuild-revision.txt\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: initial"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "tag", "v1"], check=True)
    monkeypatch.setattr(installer, "target_revision", lambda server: subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip())
    return repo


@pytest.mark.parametrize("installer", [False, True], indirect=True)
def test_install_update_rollback_and_uninstall(installer, monkeypatch, repository):
    service = mock_runtime(installer, monkeypatch)
    installer.check_owned()
    installer.install(arguments(repository=str(repository), ref="v1"))
    root = installer.ROOT
    identity = (root / "data/config.ini").read_bytes()
    old = (root / "current").resolve()
    old_manager = (root / "bin/installer.py").read_bytes()
    old_service = installer.SERVICE.read_bytes()
    stamp = old / "source/libs/python/common/src/drastic_common/build-version.txt"
    installed_version = stamp.read_text().strip()
    assert installed_version.endswith(installer.read_state()["commit"][:8])
    assert installer.read_state()["version"] == installed_version
    env = root / "drastic-agent.env"
    env.write_text(env.read_text() + "DRASTIC_PROXMOX_TOKEN_SECRET=keep-me\n")
    assert service["active"] and service["enabled"]
    assert "secret" not in json.dumps(installer.read_state())
    assert "DRASTIC_PASSWORD=" not in env.read_text()
    assert f'RESTIC_CACHE_DIR="{root}/cache/restic"' in env.read_text()
    wrapper_setup = (root / "bin/drastic-agent").read_text().rsplit("\nexec ", 1)[0]
    exported = subprocess.run(
        ["bash", "-c", wrapper_setup + '\nprintf "%s\\n" "$DRASTIC_AGENT_INSTALL_SOURCE" "$RESTIC_CACHE_DIR"'],
        check=True, capture_output=True, text=True,
    )
    assert exported.stdout.splitlines() == ["git", str(root / "cache/restic")]
    installer.check_owned()

    candidate_manager = repository / "scripts/drastic-agent-installer.py"
    candidate_manager.write_text(Path(installer.__file__).read_text() + "\n# Updated controller\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: update controller"], check=True, capture_output=True)
    service["fail_start"] = True
    with pytest.raises(RuntimeError, match="failed to start"):
        installer.install(arguments("update", server=None))
    assert (root / "current").resolve() == old
    assert stamp.read_text().strip() == installed_version
    assert service["active"] and service["enabled"]
    assert list((root / "releases").iterdir()) == [old]
    assert (root / "data/config.ini").read_bytes() == identity
    assert "keep-me" in env.read_text()
    assert (root / "bin/installer.py").read_bytes() == old_manager
    assert installer.SERVICE.read_bytes() == old_service
    failed = json.loads(next((root / "data").glob("update-*.json")).read_text())
    assert failed["state"] == "failed"
    assert [log["message"] for log in failed["logs"]][-2:] == [
        "Previous installation restored", "Agent update failed: The new agent service failed to start.",
    ]

    installer.install(arguments("update", server=None))
    assert not old.exists()
    assert installer.read_state()["ref"] == installer.read_state()["commit"]
    assert service["restic_checks"] == 3
    assert (root / "bin/installer.py").read_bytes() == candidate_manager.read_bytes()
    assert (root / "data/config.ini").read_bytes() == identity
    assert "keep-me" in env.read_text()
    updates = [json.loads(path.read_text()) for path in (root / "data").glob("update-*.json")]
    succeeded = next(update for update in updates if update["state"] == "success")
    assert succeeded["uuid"] != failed["uuid"]
    assert succeeded["ended"] >= succeeded["started"]
    assert succeeded["logs"][-1]["message"] == "Agent update completed; startup checks passed"

    service["refuse_stop"] = True
    with pytest.raises(RuntimeError, match="still running"):
        installer.uninstall(arguments(purge=True))
    assert (root / "current").exists() and installer.SERVICE.exists()
    service["refuse_stop"] = False
    if installer.MACOS:
        with pytest.raises(RuntimeError, match="still running"):
            installer.uninstall(arguments(purge=True))
        service["active"] = False  # The unloaded process eventually finishes shutdown.
    service["stop_error"] = True
    installer.uninstall(arguments())
    assert not installer.WRAPPER.is_symlink() and not installer.SERVICE.exists()
    assert (root / "data/config.ini").read_bytes() == identity
    assert set(path.name for path in root.iterdir()) == {
        "data", "drastic-agent.env", "install.json", installer.MARKER,
    }
    installer.check_owned()
    installer.uninstall(arguments(purge=True))
    assert not root.exists()


def test_container_uses_prebuilt_release_and_shared_update_rollback(installer, monkeypatch, repository, tmp_path):
    data = tmp_path / "mounted-data"
    data.mkdir()
    identity = b"[AGENT]\nidentifier = retained\nsecret = retained-secret\n[SERVER]\nurl = https://backup.example.net\n"
    (data / "config.ini").write_bytes(identity)
    monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(data))
    monkeypatch.setenv("DRASTIC_PROXMOX_TOKEN_SECRET", "keep-me")
    image = installer.ROOT / "releases/image"
    image.mkdir(parents=True)
    stamp = image / "source/libs/python/common/src/drastic_common/build-version.txt"
    stamp.parent.mkdir(parents=True)
    image_commit = "a" * 40
    stamp.write_text("2026-10-10-aaaaaaaa\n")
    stamp.with_name("build-revision.txt").write_text(image_commit + "\n")
    installer.initialize_image(argparse.Namespace(repository=str(repository)))
    installer.check_owned()
    assert not installer.SERVICE.exists()
    assert (installer.ROOT / "current").resolve() == image
    assert not (installer.ROOT / "data").exists()
    assert installer.read_state()["deployment"] == "docker"
    assert installer.read_state()["commit"] == installer.read_state()["ref"] == image_commit
    assert installer.read_state()["version"] == "2026-10-10-aaaaaaaa"
    old_manager = (installer.ROOT / "bin/installer.py").read_bytes()
    candidate_manager = repository / "scripts/drastic-agent-installer.py"
    candidate_manager.write_text(Path(installer.__file__).read_text() + "\n# Updated Docker controller\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: update Docker controller"], check=True, capture_output=True)
    mock_runtime(installer, monkeypatch)
    processes = []
    fail_start = False

    class Process:
        def __init__(self, command, env):
            nonlocal fail_start
            self.returncode = 1 if fail_start else None
            fail_start = False
            self.env = env
            processes.append(self)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    monkeypatch.setattr(installer, "subprocess", SimpleNamespace(**{**vars(subprocess), "Popen": Process}))
    installer.start()
    fail_start = True
    with pytest.raises(RuntimeError, match="failed to start"):
        installer.install(arguments("update", server=None))
    assert (installer.ROOT / "current").resolve() == image
    assert installer.active()
    assert installer.read_state()["commit"] == image_commit
    assert (installer.ROOT / "bin/installer.py").read_bytes() == old_manager

    installer.install(arguments("update", server=None))
    current = (installer.ROOT / "current").resolve()
    assert current != image and not image.exists()
    assert not installer.SERVICE.exists()
    assert processes[-1].env["DRASTIC_AGENT_DATA_DIR"] == str(data)
    assert processes[-1].env["DRASTIC_PROXMOX_TOKEN_SECRET"] == "keep-me"
    assert (data / "config.ini").read_bytes() == identity
    assert installer.read_state()["deployment"] == "docker"
    assert (installer.ROOT / "bin/installer.py").read_bytes() == candidate_manager.read_bytes()

    dependencies = repository / "scripts/install-agent-dependencies.sh"
    dependencies.write_text("#!/bin/bash\nexit 9\n")
    subprocess.run(["git", "-C", str(repository), "add", str(dependencies)], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: required packages fail"], check=True, capture_output=True)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(arguments("update", server=None, ref="main"))
    assert (installer.ROOT / "current").resolve() == current
    assert installer.active()
    assert (data / "config.ini").read_bytes() == identity
    updates = [json.loads(path.read_text()) for path in data.glob("update-*.json")]
    assert sorted(update["state"] for update in updates) == ["failed", "failed", "success"]
    assert any(update["logs"][-1]["message"] == "Agent update failed: bash exited with code 9" for update in updates)
    assert any("Previous installation restored" in [log["message"] for log in update["logs"]] for update in updates)


def test_container_update_request_requires_launcher_and_rejects_duplicates(installer):
    args = arguments("update", server=None, user=None, password=None, ref="develop")
    with pytest.raises(RuntimeError, match="launcher is not running"):
        installer.request_update(args)
    (installer.ROOT / "launcher.pid").write_text(str(os.getpid()))
    installer.request_update(args)
    request = installer.ROOT / "update-request.json"
    assert json.loads(request.read_text()) == {"repository": None, "ref": "develop", "source": None, "revision": None}
    with pytest.raises(RuntimeError, match="already running"):
        installer.request_update(args)
    request.unlink()
    with pytest.raises(RuntimeError, match="preserve registration"):
        installer.request_update(arguments("update"))
    assert not request.exists()


def test_container_launcher_processes_request_and_handles_shutdown_with_stale_own_pid(installer, monkeypatch):
    monkeypatch.setattr(installer, "CONTAINER", True)
    pid = installer.ROOT / "launcher.pid"
    pid.write_text(str(os.getpid()))  # A recreated PID namespace can reuse the old launcher's PID.
    request = installer.ROOT / "update-request.json"
    request.write_text(json.dumps({"repository": None, "ref": "develop", "source": None}))
    handlers = {}
    calls = []
    monkeypatch.setattr(installer, "signal", SimpleNamespace(
        SIGTERM=signal.SIGTERM, SIGINT=signal.SIGINT,
        signal=lambda number, handler: handlers.update({number: handler})))
    monkeypatch.setattr(installer, "start", lambda: calls.append("start"))
    monkeypatch.setattr(installer, "active", lambda: True)
    monkeypatch.setattr(installer, "stop", lambda: calls.append("stop"))

    def update(args):
        assert request.exists()
        assert args.command == "update" and args.ref == "develop"
        calls.append("update")

    monkeypatch.setattr(installer, "install", update)
    monkeypatch.setattr(installer.time, "sleep", lambda _: handlers[signal.SIGTERM](signal.SIGTERM, None))
    installer.run_container()
    assert calls == ["start", "update", "stop"]
    assert installer.SHUTTING_DOWN
    assert not request.exists() and not pid.exists()


def test_update_rolls_back_when_stop_reports_error_but_service_is_inactive(installer, monkeypatch, repository):
    service = mock_runtime(installer, monkeypatch)
    installer.install(arguments(repository=str(repository), ref="v1"))
    old = (installer.ROOT / "current").resolve()
    service["stop_error"] = True
    service["fail_start"] = True

    with pytest.raises(RuntimeError, match="failed to start"):
        installer.install(arguments("update", server=None))

    assert (installer.ROOT / "current").resolve() == old
    assert list((installer.ROOT / "releases").iterdir()) == [old]
    assert service["active"]


@pytest.mark.parametrize("installer", [False, True], indirect=True)
def test_update_preserves_both_releases_when_rollback_cannot_stop_service(installer, monkeypatch, repository):
    service = mock_runtime(installer, monkeypatch)
    installer.install(arguments(repository=str(repository), ref="v1"))
    old = (installer.ROOT / "current").resolve()
    was_active = installer.active
    failed = False

    def fail_health_check_once():
        nonlocal failed
        if (installer.ROOT / "current").resolve() != old and not failed:
            failed = True
            service["refuse_stop"] = True
            raise RuntimeError("health check failed")
        return was_active()

    monkeypatch.setattr(installer, "active", fail_health_check_once)
    with pytest.raises(RuntimeError, match="Rollback blocked.*retry the installation"):
        installer.install(arguments("update", ref="main", server=None))

    current = (installer.ROOT / "current").resolve()
    assert current != old
    assert set((installer.ROOT / "releases").iterdir()) == {old, current}
    assert installer.read_state()["ref"] == "main"
    assert installer.SERVICE.exists() and service["active"]


def test_foreign_paths_are_not_removed(installer, tmp_path):
    installer.SERVICE.write_text("foreign service")
    with pytest.raises(RuntimeError, match="not managed"):
        installer.check_owned()
    installer.SERVICE.unlink()
    external = tmp_path / "external"
    external.mkdir()
    (installer.ROOT / "data").symlink_to(external)
    with pytest.raises(RuntimeError, match="Unexpected symlink"):
        installer.check_owned()
    assert external.exists()


@pytest.mark.parametrize("installer", [False, True], indirect=True)
def test_updates_replace_legacy_ref_with_exact_backend_commit_and_preserve_service_on_failure(installer, monkeypatch, repository):
    service = mock_runtime(installer, monkeypatch)
    subprocess.run(["git", "-C", str(repository), "checkout", "-b", "develop"], check=True)
    installer.install(arguments(repository=str(repository), ref="develop"))
    old_commit = installer.read_state()["commit"]
    old_version = installer.read_state()["version"]
    (repository / "version").write_text("two")
    subprocess.run(["git", "-C", str(repository), "add", "version"], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: next"], check=True, capture_output=True)
    backend_commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.setattr(installer, "target_revision", lambda server: backend_commit)
    # The remote default branch can advance beyond the deployed backend.
    subprocess.run(["git", "-C", str(repository), "checkout", "main"], check=True)
    (repository / "version").write_text("newer than backend")
    subprocess.run(["git", "-C", str(repository), "add", "version"], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: newer remote"], check=True, capture_output=True)
    installer.install(arguments("update", server=None))
    assert installer.read_state()["commit"] != old_commit
    assert installer.read_state()["version"] != old_version
    assert installer.read_state()["version"].endswith(installer.read_state()["commit"][:8])
    assert installer.read_state()["commit"] == backend_commit
    assert installer.read_state()["ref"] == backend_commit
    assert (installer.ROOT / "current/source/version").read_text() == "two"
    current = (installer.ROOT / "current").resolve()
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(arguments("update", ref="nonexistent-ref", server=None))
    assert (installer.ROOT / "current").resolve() == current
    assert list((installer.ROOT / "releases").iterdir()) == [current]
    assert service["active"]
    # An unavailable backend commit also leaves the current release and identity untouched.
    monkeypatch.setattr(installer, "target_revision", lambda server: "f" * 40)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(arguments("update", server=None))
    assert (installer.ROOT / "current").resolve() == current
    assert service["active"]


@pytest.mark.parametrize("installer", [False, True], indirect=True)
def test_restic_preflight_failure_keeps_running_release_and_identity(installer, monkeypatch, repository):
    service = mock_runtime(installer, monkeypatch)
    installer.install(arguments(repository=str(repository), ref="v1"))
    old = (installer.ROOT / "current").resolve()
    identity = (installer.ROOT / "data/config.ini").read_bytes()
    state = installer.read_state()
    service["fail_restic"] = True
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(arguments("update", server=None))
    assert service["active"]
    assert (installer.ROOT / "current").resolve() == old
    assert list((installer.ROOT / "releases").iterdir()) == [old]
    assert installer.read_state() == state
    assert (installer.ROOT / "data/config.ini").read_bytes() == identity
    status = json.loads(next((installer.ROOT / "data").glob("update-*.json")).read_text())
    assert status["state"] == "failed"
    assert not any("Stopping agent" in log["message"] for log in status["logs"])


@pytest.mark.parametrize("installer", [True], indirect=True)
def test_macos_rejects_incompatible_target_before_stopping(installer, monkeypatch, repository):
    service = mock_runtime(installer, monkeypatch)
    installer.install(arguments(repository=str(repository), ref="v1"))
    current = (installer.ROOT / "current").resolve()
    controller = (installer.ROOT / "bin/installer.py").read_bytes()
    (repository / "scripts/drastic-agent-installer.py").write_text("\nINDEPENDENT_LIFECYCLE = True\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: incompatible release"], check=True, capture_output=True)
    with pytest.raises(RuntimeError, match="does not support macOS"):
        installer.install(arguments("update", server=None))
    assert service["active"]
    assert (installer.ROOT / "current").resolve() == current
    assert (installer.ROOT / "bin/installer.py").read_bytes() == controller
    assert list((installer.ROOT / "releases").iterdir()) == [current]


@pytest.mark.parametrize("installer", [True], indirect=True)
def test_macos_service_environment_and_permission_paths(installer, monkeypatch, capsys):
    root = installer.ROOT
    (root / 'data').mkdir(mode=0o700)
    python = root / "tools/python/test/bin/python3"
    python.parent.mkdir(parents=True)
    python.touch()
    venv = root / "current/venv/bin/python"
    venv.parent.mkdir(parents=True)
    venv.symlink_to(python)
    constant = root / "current/source/libs/python/common/src/drastic_common/restic/constants.py"
    constant.parent.mkdir(parents=True)
    constant.write_text('RESTIC_VERSION = "0.19.1"\n')
    (root / 'drastic-agent.env').write_text(
        '# Settings\nDRASTIC_AGENT_DATA_DIR="/opt/drastic-agent/data"\n'
        'DRASTIC_SERVER="https://example.net"\nCUSTOM="spaces # and quotes \\"ok\\""\n'
    )
    called = []
    monkeypatch.setattr(installer.os, "execve", lambda *args: called.append(args))
    monkeypatch.setattr(installer.platform, "machine", lambda: "arm64")
    installer.run_macos()
    executable, args, env = called[0]
    assert executable == venv and args == [str(venv), "-m", "drastic_agent"]
    assert env["CUSTOM"] == 'spaces # and quotes "ok"'
    assert env["DRASTIC_AGENT_DATA_DIR"] == "/opt/drastic-agent/data"
    assert "/usr/bin" in env["PATH"].split(":")
    installer.macos_permissions()
    output = capsys.readouterr().out
    assert str(python) in output
    assert str(root / "tools/restic/restic_0.19.1_darwin_arm64") in output
    assert (root / 'data').stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("installer", [True], indirect=True)
def test_macos_updater_is_independent_and_rejects_duplicate_or_uncertain_jobs(installer, monkeypatch):
    (installer.ROOT / "bin").mkdir()
    state = None
    calls = []
    monkeypatch.setattr(installer, "launchd_state", lambda label: state)
    monkeypatch.setattr(installer, "run", lambda *args, **kwargs: calls.append(args))
    installer.start_macos_update()
    plist = plistlib.loads((installer.ROOT / "bin/update.plist").read_bytes())
    assert plist["Label"] == "net.drastic.agent.update"
    assert plist["ProgramArguments"][-2:] == ["log", "update"]
    assert plist["RunAtLoad"] and not plist["KeepAlive"]
    assert "current" not in plist["ProgramArguments"][1]
    assert calls[-1] == ("launchctl", "bootstrap", "system", installer.ROOT / "bin/update.plist")
    calls.clear()
    for job_state in ("state = running\npid = 42", "state = spawn scheduled", "state = not running",
                      "state = not running\nlast exit code = (never exited)"):
        state = job_state
        with pytest.raises(RuntimeError, match="already running"):
            installer.start_macos_update()
        assert not calls
    state = "state = not running\nlast exit code = 1"
    installer.start_macos_update()
    assert calls[0] == ("launchctl", "bootout", "system/net.drastic.agent.update")


@pytest.mark.parametrize("installer", [True], indirect=True)
def test_macos_launchctl_errors_do_not_allow_removal(installer, monkeypatch):
    installer.SERVICE.write_text(installer.service_text())
    monkeypatch.setattr(installer, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 1, stdout="", stderr="Not privileged"))
    with pytest.raises(RuntimeError, match="Could not inspect"):
        installer.uninstall(arguments(purge=True))
    assert installer.ROOT.exists() and installer.SERVICE.exists()


@pytest.mark.parametrize("installer", [True], indirect=True)
def test_macos_update_uses_target_service_template_and_accepts_previous_python(installer, monkeypatch, repository):
    service = mock_runtime(installer, monkeypatch)
    installer.install(arguments(repository=str(repository), ref="v1"))
    old_definition = installer.SERVICE.read_bytes()
    target = Path(installer.__file__).read_text().replace("'ThrottleInterval': 5", "'ThrottleInterval': 9")
    (repository / "scripts/drastic-agent-installer.py").write_text(target)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: changed service template"], check=True, capture_output=True)
    installer.install(arguments("update", server=None))
    assert service["active"]
    assert plistlib.loads(installer.SERVICE.read_bytes())["ThrottleInterval"] == 9
    installer.check_owned()
    # A new bootstrap interpreter must recognize services installed by an older private Python.
    monkeypatch.setattr(installer.sys, "executable", str(installer.ROOT / "tools/python/new/bin/python3"))
    installer.check_owned()
    installer.SERVICE.write_bytes(old_definition)
    installer.check_owned()


@pytest.mark.parametrize("installer", [True], indirect=True)
@pytest.mark.parametrize("field,value", [
    ("Label", "foreign.agent"),
    ("ProgramArguments", ["/usr/bin/python3", "/foreign/installer.py", "run"]),
])
def test_macos_foreign_service_is_rejected(installer, field, value):
    definition = plistlib.loads(installer.service_text().encode())
    definition[field] = value
    installer.SERVICE.write_bytes(plistlib.dumps(definition))
    with pytest.raises(RuntimeError, match="not managed"):
        installer.check_owned()


@pytest.mark.parametrize("installer", [True], indirect=True)
@pytest.mark.parametrize("unsafe", ["writable", "foreign-owner", "symlink"])
def test_macos_service_ownership_checks_remain_strict(installer, monkeypatch, unsafe):
    installer.SERVICE.write_text(installer.service_text())
    if unsafe == "writable":
        installer.SERVICE.chmod(0o666)
    elif unsafe == "foreign-owner":
        monkeypatch.setattr(installer.os, "geteuid", lambda: installer.SERVICE.stat().st_uid + 1)
    else:
        target = installer.SERVICE.with_suffix('.foreign')
        installer.SERVICE.rename(target)
        installer.SERVICE.symlink_to(target)
    with pytest.raises(RuntimeError, match="not managed"):
        installer.check_owned()


@pytest.mark.parametrize("installer", [True], indirect=True)
def test_macos_tool_paths_are_readable_with_private_umask(installer, monkeypatch, repository):
    mock_runtime(installer, monkeypatch)
    private = installer.ROOT / "data"
    private.mkdir(mode=0o700)
    secret = private / "config.ini"
    secret.write_text("[AGENT]\nidentifier = retained\n[SERVER]\nurl = https://backup.example.net\n")
    secret.chmod(0o600)
    python = Path(installer.sys.executable)
    for parent in python.parents:
        parent.chmod(0o700)
        if parent == installer.ROOT:
            break
    previous = os.umask(0o077)
    try:
        installer.install(arguments(repository=str(repository), ref="v1"))
    finally:
        os.umask(previous)
    restic = installer.ROOT / "tools/restic/restic_0.19.1_darwin_arm64"
    for program in (python, restic):
        assert program.stat().st_mode & 0o555 == 0o555
        for parent in program.parents:
            assert parent.stat().st_mode & 0o555 == 0o555
            if parent == installer.ROOT:
                break
    assert private.stat().st_mode & 0o777 == 0o700
    assert secret.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("escape", ["directory", "nested-directory", "file", "return-link"])
def test_macos_permission_helper_rejects_external_targets_before_any_chmod(installer, tmp_path, escape):
    outside = tmp_path / "private-tools"
    outside.mkdir(mode=0o700)
    external_program = outside / "python3"
    external_program.write_text("private executable")
    external_program.chmod(0o640)
    valid_program = installer.ROOT / "tools/bin/program"
    valid_program.parent.mkdir(parents=True)
    valid_program.write_text("managed executable")
    valid_program.chmod(0o600)
    if escape == "file":
        program = installer.ROOT / "tools/python3"
        program.symlink_to(external_program)
    else:
        link = installer.ROOT / ("tools/python/package" if escape == "nested-directory" else "tools/restic")
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside, target_is_directory=True)
        if escape == "return-link":
            external_program.unlink()
            external_program.symlink_to(valid_program)
        program = link / external_program.name
    original = (outside.stat().st_mode, external_program.lstat().st_mode,
                external_program.read_bytes(), valid_program.stat().st_mode)
    with pytest.raises(RuntimeError, match="must resolve inside"):
        installer.macos_program_permissions(valid_program, program)
    assert (outside.stat().st_mode, external_program.lstat().st_mode,
            external_program.read_bytes(), valid_program.stat().st_mode) == original


@pytest.mark.parametrize("tool_path", ["tools/bin", "tools/python", "tools/restic"])
def test_tool_directory_links_are_rejected_before_bootstrap_writes(installer, tmp_path, tool_path):
    outside = tmp_path / "private-tools"
    outside.mkdir(mode=0o700)
    executable = outside / "program"
    executable.write_text("private contents")
    executable.chmod(0o640)
    link = installer.ROOT / tool_path
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    original = (outside.stat().st_mode, executable.stat().st_mode, executable.read_bytes())
    with pytest.raises(RuntimeError, match="Unexpected symlink"):
        installer.check_owned()
    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    guard = script.read_text().split("<<'CHECK'\n", 1)[1].split("\nCHECK", 1)[0]
    guard = (guard.replace("/opt/drastic-agent", str(installer.ROOT))
             .replace("/opt", str(tmp_path))
             .replace("= 0 ]", f"= {os.getuid()} ]"))
    result = subprocess.run(["bash", "-s", "--", "false"], input=guard, text=True, capture_output=True)
    assert result.returncode != 0
    assert "Private runtime paths must not be symlinks" in result.stderr
    assert set(path.name for path in outside.iterdir()) == {"program"}
    assert (outside.stat().st_mode, executable.stat().st_mode, executable.read_bytes()) == original


@pytest.mark.parametrize("installer", [True], indirect=True)
@pytest.mark.parametrize("command", ["run", "update"])
def test_macos_logs_rotate_large_output_and_preserve_startup_failure(installer, command):
    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    (installer.ROOT / "bin").mkdir()
    (installer.ROOT / "data").mkdir(mode=0o700)
    (installer.ROOT / "bin/installer.py").write_text(
        'import subprocess, sys\n'
        'assert sys.argv[1:] == ' + repr([command, *(["--wait-lock"] if command == "update" else [])]) + '\n'
        'subprocess.run([sys.executable, "-c", "import os; os.write(1, b\'x\' * (4 * 1024 * 1024))"], check=True)\n'
        'sys.stderr.write("startup failed\\n")\nraise SystemExit(7)\n'
    )
    assert installer.run_logged(command) == 7
    label = installer.SERVICE_LABEL if command == "run" else installer.UPDATE_LABEL
    files = list((installer.ROOT / "data").glob(f"{label}.log*"))
    assert len(files) == 3
    assert all(path.stat().st_size <= 1024 * 1024 for path in files)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    assert (installer.ROOT / "data" / f"{label}.log").read_text().endswith("startup failed\n")
    assert {sig: signal.getsignal(sig) for sig in previous_handlers} == previous_handlers


@pytest.mark.parametrize("installer", [True], indirect=True)
@pytest.mark.parametrize("command", ["run", "update"])
def test_macos_log_wrapper_rejects_symlinked_data_without_touching_target(installer, monkeypatch, tmp_path, command):
    outside = tmp_path / "foreign-data"
    outside.mkdir()
    label = installer.SERVICE_LABEL if command == "run" else installer.UPDATE_LABEL
    for suffix in ("", ".1", ".2"):
        path = outside / f"{label}.log{suffix}"
        path.write_text("foreign log contents\n")
        path.chmod(0o640)
    original = {path.name: (path.read_bytes(), path.stat().st_mode) for path in outside.iterdir()}
    (installer.ROOT / "data").symlink_to(outside, target_is_directory=True)

    def unexpected_spawn(*args, **kwargs):
        pytest.fail("An unowned installation must not spawn a worker")

    monkeypatch.setattr(installer.subprocess, "Popen", unexpected_spawn)
    with pytest.raises(RuntimeError, match="Unexpected symlink"):
        installer.run_logged(command)
    assert {path.name: (path.read_bytes(), path.stat().st_mode) for path in outside.iterdir()} == original


@pytest.mark.parametrize("installer", [True], indirect=True)
def test_macos_log_process_forwards_shutdown_and_waits_for_child(installer):
    (installer.ROOT / "bin").mkdir()
    (installer.ROOT / "data").mkdir(mode=0o700)
    ready = installer.ROOT / "ready"
    (installer.ROOT / "bin/installer.py").write_text(
        'import signal, time\nfrom pathlib import Path\n'
        'def stop(sig, frame):\n    print("child stopped", flush=True)\n    raise SystemExit(42)\n'
        'signal.signal(signal.SIGTERM, stop)\n'
        f'Path({str(ready)!r}).touch()\n'
        'time.sleep(60)\n'
    )
    supervisor = (
        'import runpy, sys; from pathlib import Path; '
        'controller = runpy.run_path(sys.argv[1]); run = controller["run_logged"]; '
        'root = Path(sys.argv[2]); '
        'run.__globals__.update(ROOT=root, SERVICE=root / "service", WRAPPER=root / "command"); '
        'sys.exit(run("run"))'
    )
    process = subprocess.Popen([sys.executable, '-c', supervisor, installer.__file__, str(installer.ROOT)])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        process.terminate()
        assert process.wait(timeout=5) == 42
        assert (installer.ROOT / "data/net.drastic.agent.log").read_text() == "child stopped\n"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize("installer", [True], indirect=True)
@pytest.mark.parametrize("wait", [False, True])
def test_macos_updater_waits_for_launchers_lock_but_normal_commands_do_not(installer, monkeypatch, wait):
    monkeypatch.setattr(sys, "argv", ["installer", "update", *(["--wait-lock"] if wait else [])])
    monkeypatch.setattr(installer, "sys", SimpleNamespace(**{
        **vars(sys), "platform": "darwin",
    }))
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    attempts = []
    installed = []

    def lock(fd, flags):
        assert os.fstat(fd).st_ino == (installer.ROOT / ".installer.lock").stat().st_ino
        attempts.append(flags)
        if len(attempts) == 1:
            raise BlockingIOError("Launcher is still returning its acknowledgement")

    monkeypatch.setattr(installer.fcntl, "flock", lock)
    monkeypatch.setattr(installer, "install", lambda args: installed.append(args.command))
    if wait:
        installer.main()
        assert installed == ["update"] and len(attempts) == 2
    else:
        with pytest.raises(SystemExit) as error:
            installer.main()
        assert error.value.code == 1
        assert not installed and len(attempts) == 1


@pytest.fixture
def historical_repository(tmp_path):
    project = Path(__file__).resolve().parents[3]
    repo = tmp_path / "historical-repository"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(project), str(repo)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", "ab50d71"], check=True, capture_output=True)
    script = "scripts/drastic-agent-installer.py"
    (repo / script).write_bytes((project / script).read_bytes())
    subprocess.run(["git", "-C", str(repo), "add", script], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: backend-bound controller"], check=True, capture_output=True)
    backend_revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    return repo, backend_revision


@pytest.fixture
def legacy_download_python(tmp_path):
    # Mock only HTTP responses; the historical shell downloader still checks and installs its archive.
    python = tmp_path / "legacy-download-python"
    binary = b"#!/bin/sh\nprintf '%s\\n' 'restic 0.18.1 compiled for test'\n"
    archive = bz2.compress(binary)
    python.write_text(f'''#!{sys.executable}
import hashlib, io, os, sys
import urllib.request
if sys.argv[1] != "-":
    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
sys.argv = sys.argv[1:]
archive = {archive!r}
def urlopen(url, timeout):
    checksum = "0" * 64 if os.environ.get("TEST_BAD_RESTIC_HASH") else hashlib.sha256(archive).hexdigest()
    payload = (checksum + " " + sys.argv[2] + "\\n").encode() if url.endswith("/SHA256SUMS") else archive
    return io.BytesIO(payload)
urllib.request.urlopen = urlopen
exec(compile(sys.stdin.read(), "<historical-downloader>", "exec"), {{"__name__": "__main__"}})
''')
    python.chmod(0o755)
    return python


@pytest.mark.parametrize("ref", ["b56f2df", "ab50d71"])
@pytest.mark.parametrize("docker", [False, True])
def test_real_older_release_keeps_backend_controller_for_next_update(
    installer, monkeypatch, historical_repository, legacy_download_python, tmp_path, ref, docker,
):
    repo, backend_revision = historical_repository
    service = mock_runtime(installer, monkeypatch, mock_packages=True)
    monkeypatch.setattr(installer, "sys", SimpleNamespace(executable=str(legacy_download_python)))
    data = tmp_path / "mounted-data" if docker else installer.ROOT / "data"
    data.mkdir()
    identity = b"[AGENT]\nidentifier = preserved\nsecret = preserved-secret\n[SERVER]\nurl = https://backup.example.net\n"
    (data / "config.ini").write_bytes(identity)
    if docker:
        monkeypatch.setenv("DRASTIC_AGENT_DATA_DIR", str(data))
        monkeypatch.setattr(installer, "CONTAINER", True)
        monkeypatch.setattr(installer, "active", lambda: service["active"])
        monkeypatch.setattr(installer, "start", lambda: service.update(active=True))
        monkeypatch.setattr(installer, "stop", lambda: service.update(active=False))
    installer.install(arguments(repository=str(repo), ref=backend_revision))
    controller = installer.ROOT / "bin/installer.py"
    saved_controller = controller.read_bytes()
    installer.install(arguments("update", ref=ref, server=None))
    legacy_source = installer.ROOT / "current/source"
    # These are real old sources, not a stub that already implements the new policy.
    assert 'ref = args.ref or state.get(\'ref\') or \'main\'' in (legacy_source / "scripts/drastic-agent-installer.py").read_text()
    legacy_commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", ref], text=True).strip()
    assert installer.read_state()["commit"] == legacy_commit
    assert controller.read_bytes() == saved_controller
    wrapper = (installer.ROOT / "bin/drastic-agent").read_text()
    assert f'"{controller}" "$@"' in wrapper
    assert "current/source/scripts/drastic-agent-installer.py" not in wrapper
    assert (data / "config.ini").read_bytes() == identity
    if ref == "b56f2df":
        assert not (legacy_source / "apps/agent/src/drastic_agent/integrations/restic_binary.py").exists()
        binary = next((data / "bin").glob("restic_0.18.1_linux_*"))
        assert subprocess.check_output([str(binary), "version"], text=True).startswith("restic 0.18.1 ")

    # Reload the controller as a new CLI invocation (or a restarted Docker launcher) would.
    spec = importlib.util.spec_from_file_location("installed_controller", controller)
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    for name in ("ROOT", "WRAPPER", "SERVICE", "CONTAINER"):
        monkeypatch.setattr(manager, name, getattr(installer, name))
    monkeypatch.setattr(manager.time, "sleep", lambda _: None)
    monkeypatch.setattr(manager, "target_revision", lambda server: backend_revision)
    mock_runtime(manager, monkeypatch, mock_packages=True)
    if docker:
        monkeypatch.setattr(manager, "active", lambda: service["active"])
        monkeypatch.setattr(manager, "start", lambda: service.update(active=True))
        monkeypatch.setattr(manager, "stop", lambda: service.update(active=False))
    manager.install(arguments("update", server=None))
    assert manager.read_state()["commit"] == backend_revision
    assert manager.read_state()["ref"] == backend_revision
    assert (data / "config.ini").read_bytes() == identity


def test_historical_download_failure_keeps_active_binary_and_release(
    installer, monkeypatch, historical_repository, legacy_download_python,
):
    repo, backend_revision = historical_repository
    service = mock_runtime(installer, monkeypatch, mock_packages=True)
    monkeypatch.setattr(installer, "sys", SimpleNamespace(executable=str(legacy_download_python)))
    installer.install(arguments(repository=str(repo), ref=backend_revision))
    old_release = (installer.ROOT / "current").resolve()
    old_state = installer.read_state()
    controller = (installer.ROOT / "bin/installer.py").read_bytes()
    identity = (installer.ROOT / "data/config.ini").read_bytes()
    binary_dir = installer.ROOT / "data/bin"
    binary_dir.mkdir()
    active_binary = binary_dir / "restic_0.19.1_linux_amd64"
    active_binary.write_bytes(b"existing active executable")
    monkeypatch.setenv("TEST_BAD_RESTIC_HASH", "1")
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(arguments("update", ref="b56f2df", server=None))
    assert service["active"]
    assert (installer.ROOT / "current").resolve() == old_release
    assert installer.read_state() == old_state
    assert (installer.ROOT / "bin/installer.py").read_bytes() == controller
    assert (installer.ROOT / "data/config.ini").read_bytes() == identity
    assert active_binary.read_bytes() == b"existing active executable"
    assert list(binary_dir.iterdir()) == [active_binary]


@pytest.mark.parametrize("payload,valid", [
    ({"revision": "a" * 40, "version": "2026-10-10-aaaaaaaa"}, True),
    ({"revision": "a" * 40, "version": "2026-10-10-aaaaaaaa-dirty"}, False),
    ({"revision": "a" * 40, "version": "2026-10-10-bbbbbbbb"}, False),
    ({"revision": "-main", "version": "unknown"}, False),
    ({"revision": "a" * 40, "version": None}, False),
    ({}, False), ([], False), ("not-json", False),
])
def test_backend_target_validation(installer, monkeypatch, payload, valid):
    def open_target(url, timeout):
        assert url == "https://backup.example.net/api/agents/installation-target"
        assert timeout == 30
        return io.BytesIO((json.dumps(payload) if payload != "not-json" else payload).encode())

    monkeypatch.setattr(installer, "build_opener", lambda handler: SimpleNamespace(open=open_target))
    if valid:
        assert installer.target_revision("https://backup.example.net/") == "a" * 40
    else:
        with pytest.raises(RuntimeError):
            installer.target_revision("https://backup.example.net")


def test_local_source_preserves_dirty_version_without_git(installer, monkeypatch, repository):
    mock_runtime(installer, monkeypatch)
    project = repository / "apps/agent/pyproject.toml"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.touch()
    installer.install(arguments(source=str(repository)))
    release = (installer.ROOT / "current").resolve()
    assert not (release / "source/.git").exists()
    stamp = release / "source/libs/python/common/src/drastic_common/build-version.txt"
    assert stamp.read_text().strip() == installer.read_state()["version"]
    assert installer.read_state()["version"].endswith(installer.read_state()["commit"][:8] + "-dirty")


def test_preserved_identity_cannot_be_moved_to_another_server(installer):
    (installer.ROOT / "data").mkdir()
    (installer.ROOT / "data/config.ini").write_text(
        "[AGENT]\nidentifier = existing\n[SERVER]\nurl = https://original.example.net\n"
    )
    with pytest.raises(RuntimeError, match="server cannot change"):
        installer.install(arguments())


def test_wrapper_help_includes_uninstall(installer):
    runtime = installer.ROOT / "current/venv/bin/drastic-agent"
    runtime.parent.mkdir(parents=True)
    runtime.write_text('#!/bin/sh\nexit 0\n')
    runtime.chmod(0o755)
    installer.write_wrapper({})

    result = subprocess.run([str(installer.WRAPPER), "--help"],
                            capture_output=True, text=True, check=True)
    assert "uninstall [--purge] [--yes]" in result.stdout


def test_pipe_help_needs_no_root_or_downloads():
    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    result = subprocess.run(["bash", "-s", "--", "--help"], input=script.read_text(),
                            capture_output=True, text=True, check=True)
    assert "--ref BRANCH|TAG|COMMIT" in result.stdout


def test_piped_bootstrap_never_prompts_for_branch_and_preserves_explicit_ref():
    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    prefix = script.read_text().split("\nroot() {", 1)[0] + '\nprintf "%s\\n" "$REF"\n'
    env = {**os.environ, "DRASTIC_AGENT_REF": ""}
    for args, expected in [((), ""), (("--ref", "v1.2.3"), "v1.2.3")]:
        result = subprocess.run(["bash", "-s", "--", *args], input=prefix,
                                capture_output=True, text=True, check=True, env=env)
        assert result.stdout.strip() == expected
        assert "Git branch" not in result.stderr

@pytest.mark.parametrize("dirty,explicit_ref", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("os_name", ["Linux", "Darwin"])
def test_bootstrap_fetches_backend_commit_and_rejects_dirty_target(installer, repository, tmp_path, dirty, explicit_ref, os_name):
    manager = repository / "scripts/drastic-agent-installer.py"
    manager.write_text("raise SystemExit('Legacy controller must not run')\n")
    backend_manager = (
        '\nINDEPENDENT_LIFECYCLE = True\n'
        '\nMACOS_LIFECYCLE = True\n'
        'import json, subprocess, sys\n'
        'source = sys.argv[sys.argv.index("--source") + 1]\n'
        'print(json.dumps({"args": sys.argv[1:], "commit": subprocess.check_output('
        '["git", "-C", source, "rev-parse", "HEAD"], text=True).strip()}))\n'
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: bootstrap target"], check=True, capture_output=True)
    revision = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
    target = {"revision": revision, "version": f"2026-10-10-{revision[:8]}" + ("-dirty" if dirty else "")}
    (repository / "version").write_text("newer remote")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: ahead of backend"], check=True, capture_output=True)
    uv = installer.ROOT / "tools/bin/uv"
    uv.parent.mkdir(parents=True)
    uv.write_text(f'#!/bin/sh\nif [ "$2" = find ]; then printf "%s\\n" "{sys.executable}"; fi\n')
    uv.chmod(0o755)
    tools = tmp_path / "test-bin"
    tools.mkdir()
    for name, body in {
        "uname": f'printf "%s\\n" "{os_name}"',
        "stat": f'if [ "$2" = %Lp ]; then printf "755\\n"; else printf "%s\\n" "{os.getuid()}"; fi',
        "xcode-select": 'exit 0', "open": 'exit 0',
        "launchctl": 'exit 113',
    }.items():
        tool = tools / name
        tool.write_text(f'#!/bin/sh\n{body}\n')
        tool.chmod(0o755)
    curl = tools / "curl"
    curl.write_text(f'''#!{sys.executable}
from pathlib import Path
import sys
payload = {backend_manager!r} if sys.argv[-1].endswith("/install.py") else {json.dumps(target)!r}
if "-o" in sys.argv:
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(payload)
else:
    print(payload)
''')
    curl.chmod(0o755)
    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    bootstrap = (script.read_text()
                 .replace("/opt/drastic-agent", str(installer.ROOT))
                 .replace("/opt", str(tmp_path))
                 .replace("/usr/local/bin/drastic-agent", str(installer.WRAPPER))
                 .replace("/etc/systemd/system/drastic-agent.service", str(installer.SERVICE))
                 .replace('if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi', '"$@"')
                 .replace('if [ "$(id -u)" -ne 0 ]; then', 'if false; then')
                 .replace('"$(stat -c %u "$root")" = 0', f'"$(stat -c %u "$root")" = {os.getuid()}')
                 .replace('"$(stat -f %u "$root")" = 0', f'"$(stat -f %u "$root")" = {os.getuid()}'))
    result = subprocess.run(
        ["bash", "-s", "--", "--server", "https://backup.example.net", "--repository", str(repository),
         *(["--ref", revision] if explicit_ref else [])],
        input=bootstrap, text=True, capture_output=True, timeout=10,
        env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "DRASTIC_AGENT_REF": ""},
    )
    if dirty:
        assert result.returncode != 0
        assert "no reproducible agent installation target" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        installed = json.loads(result.stdout)
        assert installed["commit"] == revision
        flag = "--ref" if explicit_ref else "--revision"
        assert installed["args"][installed["args"].index(flag) + 1] == revision
        assert ("--ref" in installed["args"]) == explicit_ref


@pytest.mark.parametrize("parent_state", ["missing", "readable", "private"])
def test_macos_bootstrap_handles_opt_permissions_before_creating_agent(tmp_path, parent_state):
    parent = tmp_path / "install-parent"
    root = parent / "agent"
    if parent_state != "missing":
        parent.mkdir()
        parent.chmod(0o755 if parent_state == "readable" else 0o700)
        (parent / "keep").write_text("existing contents")
    commands = tmp_path / "commands"
    commands.mkdir()
    uname = commands / "uname"
    uname.write_text('#!/bin/sh\nprintf "Darwin\\n"\n')
    uname.chmod(0o755)
    stat = commands / "stat"
    stat.write_text(f'#!{sys.executable}\nimport os, sys\n'
                    'info = os.stat(sys.argv[-1])\n'
                    'print(format(info.st_mode & 0o7777, "o") if sys.argv[2] == "%Lp" else info.st_uid)\n')
    stat.chmod(0o755)
    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    guard = script.read_text().split("<<'CHECK'\n", 1)[1].split("\nCHECK", 1)[0]
    guard = (guard.replace("/opt/drastic-agent", str(root))
             .replace("/opt", str(parent))
             .replace("/usr/local/bin/drastic-agent", str(tmp_path / "command"))
             .replace("/Library/LaunchDaemons/net.drastic.agent.plist", str(tmp_path / "service"))
             .replace("= 0 ]", f"= {os.getuid()} ]"))
    result = subprocess.run(["bash", "-s", "--", "false"], input="umask 077\n" + guard,
                            text=True, capture_output=True,
                            env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"})
    if parent_state == "private":
        assert result.returncode != 0
        assert "Full Disk Access picker" in result.stderr and "chmod o+rx" in result.stderr
        assert not root.exists()
        assert parent.stat().st_mode & 0o777 == 0o700
    else:
        assert result.returncode == 0, result.stderr
        assert parent.stat().st_mode & 0o777 == 0o755
        assert root.stat().st_mode & 0o777 == 0o755
        assert (root / ".managed-by-drastic-agent").is_file()
    if parent_state != "missing":
        assert (parent / "keep").read_text() == "existing contents"


def test_bootstrap_requires_explicit_adoption_and_rejects_legacy_or_foreign_files(installer):
    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    guard = script.read_text().split("<<'CHECK'\n", 1)[1].split("\nCHECK", 1)[0]
    guard = (guard.replace("/opt/drastic-agent", str(installer.ROOT))
             .replace("/usr/local/bin/drastic-agent", str(installer.WRAPPER))
             .replace("/etc/systemd/system/drastic-agent.service", str(installer.SERVICE))
             .replace("= 0 ]", f"= {os.getuid()} ]"))
    marker = installer.ROOT / installer.MARKER
    marker.unlink()
    (installer.ROOT / "data").mkdir()

    def check(reuse="false"):
        return subprocess.run(["bash", "-s", "--", reuse], input=guard, text=True, capture_output=True)

    assert "--reuse-data" in check().stderr
    assert not marker.exists()
    legacy = installer.ROOT / "agentctl"
    legacy.touch()
    assert "Legacy installation" in check("true").stderr
    legacy.unlink()
    installer.SERVICE.write_text("foreign")
    assert "unmanaged command or service" in check("true").stderr
    installer.SERVICE.unlink()
    result = check("true")
    assert result.returncode == 0, result.stderr
    assert marker.exists()


@pytest.mark.parametrize("data_state", ["none", "existing", "new"])
@pytest.mark.parametrize("os_name", ["Linux", "Darwin"])
def test_failed_first_bootstrap_removes_only_new_runtime(tmp_path, data_state, os_name):
    root = tmp_path / "agent"
    if data_state == "existing":
        (root / "data").mkdir(parents=True)
        (root / "data/config.ini").write_text("original identity")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"uname": f'printf "%s\\n" "{os_name}"',
                       "stat": f'if [ "$2" = %Lp ]; then printf "755\\n"; else printf "%s\\n" "{os.getuid()}"; fi',
                       "xcode-select": 'exit 0', "launchctl": 'exit 113'}.items():
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\n{body}\n')
        tool.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text('#!/bin/sh\nmkdir -p "$TEST_ROOT/tools/bin" "$TEST_ROOT/cache/uv"\n'
                    'if [ "$TEST_MACOS" = true ]; then : > "$TEST_ROOT/.installer.lock"; fi\n'
                    'if [ "$TEST_NEW_IDENTITY" = true ]; then\n'
                    '  mkdir -p "$TEST_ROOT/data"\n'
                    '  printf "new identity" > "$TEST_ROOT/data/config.ini"\n'
                    'fi\nexit 22\n')
    curl.chmod(0o755)

    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    bootstrap = (script.read_text()
                 .replace("/opt/drastic-agent", str(root))
                 .replace("/opt", str(tmp_path))
                 .replace("/usr/local/bin/drastic-agent", str(tmp_path / "command"))
                 .replace("/etc/systemd/system/drastic-agent.service", str(tmp_path / "service"))
                 .replace("/Library/LaunchDaemons/net.drastic.agent.plist", str(tmp_path / "service"))
                 .replace('if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi', '"$@"')
                 .replace('if [ "$(id -u)" -ne 0 ]; then', 'if false; then')
                 .replace('systemctl is-active --quiet drastic-agent.service', 'false')
                 .replace('"$(stat -c %u "$root")" = 0', f'"$(stat -c %u "$root")" = {os.getuid()}')
                 .replace('"$(stat -f %u "$root")" = 0', f'"$(stat -f %u "$root")" = {os.getuid()}'))
    result = subprocess.run(
        ["bash", "-s", "--", "--source", str(tmp_path / "unused"),
         *(["--reuse-data"] if data_state == "existing" else [])],
        input=bootstrap, text=True, capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "TEST_ROOT": str(root),
             "TEST_MACOS": str(os_name == "Darwin").lower(),
             "TEST_NEW_IDENTITY": str(data_state == "new").lower()},
    )

    assert result.returncode == 22
    if data_state != "none":
        assert (root / "data/config.ini").read_text() == (
            "original identity" if data_state == "existing" else "new identity"
        )
        assert (root / ".managed-by-drastic-agent").exists()
        assert {path.name for path in root.iterdir()} == {
            "data", ".managed-by-drastic-agent", *([".installer.lock"] if os_name == "Darwin" else []),
        }
    else:
        assert not root.exists()


def test_failed_bootstrap_does_not_clean_existing_install(tmp_path):
    root = tmp_path / "agent"
    uv = root / "tools/bin/uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("#!/bin/sh\nexit 33\n")
    uv.chmod(0o755)
    (root / ".managed-by-drastic-agent").touch()
    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    bootstrap = (script.read_text()
                 .replace("/opt/drastic-agent", str(root))
                 .replace("/usr/local/bin/drastic-agent", str(tmp_path / "command"))
                 .replace("/etc/systemd/system/drastic-agent.service", str(tmp_path / "service"))
                 .replace('if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi', '"$@"')
                 .replace('if [ "$(id -u)" -ne 0 ]; then', 'if false; then')
                 .replace('"$(stat -c %u "$root")" = 0', f'"$(stat -c %u "$root")" = {os.getuid()}'))
    result = subprocess.run(["bash", "-s", "--", "--source", str(tmp_path / "unused")],
                            input=bootstrap, text=True, capture_output=True)

    assert result.returncode == 33
    assert uv.is_file() and (root / ".managed-by-drastic-agent").exists()


def test_target_release_optional_dependency_failure_does_not_block_install(installer, monkeypatch, repository, capsys):
    service = mock_runtime(installer, monkeypatch)
    script = repository / "scripts/install-agent-dependencies.sh"
    script.write_text("#!/bin/bash\nexit 9\n")
    subprocess.run(["git", "-C", str(repository), "add", str(script)], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: optional dependencies"], check=True, capture_output=True)
    installer.install(arguments(repository=str(repository), ref="main"))
    assert service["active"]
    assert "Optional dependencies were not installed" in capsys.readouterr().err


@pytest.mark.parametrize("proxmox,uid,present,tty,sudo_code,apt_code,expected", [
    (False, 0, False, False, 0, 0, "skip"),
    (True, 0, True, False, 0, 0, "skip"),
    (True, 0, False, False, 0, 0, "installed"),
    (True, 0, False, False, 0, 1, "package installation failed"),
    (True, 1000, False, False, 0, 0, "interactive terminal"),
    (True, 1000, False, True, 0, 0, "installed"),
    (True, 1000, False, True, 1, 0, "authorization was declined"),
])
def test_optional_dependencies_root_sudo_and_fallback(tmp_path, proxmox, uid, present, tty, sudo_code, apt_code, expected):
    commands = tmp_path / "bin"
    commands.mkdir()
    stub = commands / "stub"
    stub.write_text('''#!/bin/bash
case "${0##*/}" in
  id) printf '%s' "$TEST_UID" ;;
  dpkg-query) [ "$TEST_PRESENT" = 1 ] && printf 'install ok installed' ;;
  python) [ "$TEST_PRESENT" = 1 ] || [ -f "$TEST_INSTALLED" ] ;;
  sudo)
    printf 'sudo %s\\n' "$*" >> "$TEST_CALLS"
    if [ "$1" = -v ]; then [ -t 0 ] || exit 99; exit "$TEST_SUDO_CODE"; fi
    shift; exec "$@" ;;
  apt-get)
    printf 'apt %s\\n' "$*" >> "$TEST_CALLS"
    [ "$TEST_APT_CODE" = 0 ] || exit "$TEST_APT_CODE"
    case " $* " in *' install '*) : > "$TEST_INSTALLED" ;; esac ;;
esac
''')
    stub.chmod(0o755)
    for name in ("id", "dpkg-query", "sudo", "apt-get", "python", *(["pveversion"] if proxmox else [])):
        (commands / name).symlink_to(stub)
    # Supply only our tools (plus env), so this test can never run the host package manager.
    (commands / "env").symlink_to("/usr/bin/env")
    script = Path(__file__).resolve().parents[3] / "scripts/install-agent-dependencies.sh"
    source = script.read_text().replace("/usr/bin/python3", str(commands / "python"))
    env = {**os.environ, "PATH": str(commands), "TEST_UID": str(uid), "TEST_PRESENT": str(int(present)),
           "TEST_INSTALLED": str(tmp_path / "installed"), "TEST_CALLS": str(tmp_path / "calls"),
           "TEST_SUDO_CODE": str(sudo_code), "TEST_APT_CODE": str(apt_code)}
    if tty:
        master, slave = pty.openpty()
        try:
            def attach_tty():
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

            process = subprocess.Popen(["/bin/bash", "-s"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, env=env, preexec_fn=attach_tty)
            os.close(slave)
            output, error = process.communicate(source, timeout=5)
            assert process.returncode == 0
        finally:
            os.close(master)
    else:
        result = subprocess.run(["/bin/bash", "-s"], input=source, capture_output=True, text=True,
                                env=env, start_new_session=True, timeout=5)
        assert result.returncode == 0
        output, error = result.stdout, result.stderr
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    if expected == "installed":
        assert (tmp_path / "installed").exists()
        assert "--no-remove --no-install-recommends python3-guestfs libguestfs-tools python3-fuse fuse3 python3-libnbd libnbd-bin" in calls
        assert ("sudo -v" in calls) == (uid != 0)
        assert "dependencies are available" in output
    elif expected == "skip":
        assert calls == ""
    else:
        assert expected in error
        assert "Optional Proxmox dependencies unavailable" in error
        assert not (tmp_path / "installed").exists()


@pytest.mark.parametrize("exit_code", [0, 8])
def test_container_dependencies_are_required_and_shared_with_build(tmp_path, exit_code):
    commands = tmp_path / "bin"
    commands.mkdir()
    apt = commands / "apt-get"
    apt.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_CALLS"\nexit "$TEST_APT_CODE"\n')
    apt.chmod(0o755)
    script = Path(__file__).resolve().parents[3] / "scripts/install-agent-dependencies.sh"
    calls = tmp_path / "calls"
    result = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True,
                            env={**os.environ, "DRASTIC_AGENT_DEPLOYMENT": "docker", "PATH": str(commands),
                                 "TEST_CALLS": str(calls), "TEST_APT_CODE": str(exit_code)})
    assert result.returncode == exit_code
    assert calls.read_text().splitlines() == [
        "-o DPkg::Lock::Timeout=60 update",
        *(["-o DPkg::Lock::Timeout=60 install -y --no-remove --no-install-recommends ca-certificates git openssh-client"]
          if exit_code == 0 else []),
    ]
