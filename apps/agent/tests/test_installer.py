import argparse
import fcntl
import importlib.util
import json
import os
import pty
import signal
import subprocess
import termios
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def installer(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[3] / "scripts/drastic-agent-installer.py"
    spec = importlib.util.spec_from_file_location("installer", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "agent"
    root.mkdir()
    (root / module.MARKER).touch()
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "WRAPPER", tmp_path / "bin/drastic-agent")
    monkeypatch.setattr(module, "SERVICE", tmp_path / "drastic-agent.service")
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return module


def arguments(command="install", **overrides):
    return argparse.Namespace(**{
        "command": command, "source": None, "repository": None, "ref": None,
        "server": "https://backup.example.net", "user": "admin", "password": "secret",
        "yes": True, "purge": False, **overrides,
    })


def mock_runtime(installer, monkeypatch):
    service = {"active": False, "enabled": False, "fail_start": False, "refuse_stop": False, "stop_error": False}
    real_run = installer.run

    def run(*args, **kwargs):
        args = [str(arg) for arg in args]
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
def repository(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "version").write_text("one")
    (repo / "scripts").mkdir()
    (repo / "scripts/drastic-agent-installer.py").write_text("# test lifecycle manager\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: initial"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "tag", "v1"], check=True)
    return repo


def test_install_update_rollback_and_uninstall(installer, monkeypatch, repository):
    service = mock_runtime(installer, monkeypatch)
    installer.check_owned()
    installer.install(arguments(repository=str(repository), ref="v1"))
    root = installer.ROOT
    identity = (root / "data/config.ini").read_bytes()
    old = (root / "current").resolve()
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

    service["fail_start"] = True
    with pytest.raises(RuntimeError, match="failed to start"):
        installer.install(arguments("update", server=None))
    assert (root / "current").resolve() == old
    assert service["active"] and service["enabled"]
    assert list((root / "releases").iterdir()) == [old]
    assert (root / "data/config.ini").read_bytes() == identity
    assert "keep-me" in env.read_text()
    failed = json.loads(next((root / "data").glob("update-*.json")).read_text())
    assert failed["state"] == "failed"
    assert [log["message"] for log in failed["logs"]][-2:] == [
        "Previous installation restored", "Agent update failed: The new agent service failed to start.",
    ]

    installer.install(arguments("update", server=None))
    assert not old.exists()
    assert installer.read_state()["ref"] == "v1"
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
    installer.initialize_image(arguments(repository=str(repository), ref="v1", commit="image-commit"))
    installer.check_owned()
    assert not installer.SERVICE.exists()
    assert (installer.ROOT / "current").resolve() == image
    assert not (installer.ROOT / "data").exists()
    assert installer.read_state()["deployment"] == "docker"
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
    assert installer.read_state()["commit"] == "image-commit"

    installer.install(arguments("update", server=None))
    current = (installer.ROOT / "current").resolve()
    assert current != image and not image.exists()
    assert not installer.SERVICE.exists()
    assert processes[-1].env["DRASTIC_AGENT_DATA_DIR"] == str(data)
    assert processes[-1].env["DRASTIC_PROXMOX_TOKEN_SECRET"] == "keep-me"
    assert (data / "config.ini").read_bytes() == identity
    assert installer.read_state()["deployment"] == "docker"

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
    assert json.loads(request.read_text()) == {"repository": None, "ref": "develop", "source": None}
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


def test_branch_updates_follow_remote_and_build_failure_keeps_service(installer, monkeypatch, repository):
    service = mock_runtime(installer, monkeypatch)
    subprocess.run(["git", "-C", str(repository), "checkout", "-b", "develop"], check=True)
    installer.install(arguments(repository=str(repository), ref="develop"))
    old_commit = installer.read_state()["commit"]
    (repository / "version").write_text("two")
    subprocess.run(["git", "-C", str(repository), "add", "version"], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.net",
                    "commit", "-m", "test: next"], check=True, capture_output=True)
    # The selected branch need not be the repository's default branch.
    subprocess.run(["git", "-C", str(repository), "checkout", "main"], check=True)
    installer.install(arguments("update", server=None))
    assert installer.read_state()["commit"] != old_commit
    assert installer.read_state()["ref"] == "develop"
    current = (installer.ROOT / "current").resolve()
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(arguments("update", ref="nonexistent-ref", server=None))
    assert (installer.ROOT / "current").resolve() == current
    assert list((installer.ROOT / "releases").iterdir()) == [current]
    assert service["active"]


def test_preserved_identity_cannot_be_moved_to_another_server(installer):
    (installer.ROOT / "data").mkdir()
    (installer.ROOT / "data/config.ini").write_text(
        "[AGENT]\nidentifier = existing\n[SERVER]\nurl = https://original.example.net\n"
    )
    with pytest.raises(RuntimeError, match="server cannot change"):
        installer.install(arguments())


def test_pipe_help_needs_no_root_or_downloads():
    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    result = subprocess.run(["bash", "-s", "--", "--help"], input=script.read_text(),
                            capture_output=True, text=True, check=True)
    assert "--ref BRANCH|TAG|COMMIT" in result.stdout


def test_piped_bootstrap_prompts_for_ref_and_preserves_explicit_ref():
    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    prefix = script.read_text().split("\nroot() {", 1)[0] + '\nprintf "%s\\n" "$REF"\n'
    env = {**os.environ, "DRASTIC_AGENT_REF": ""}
    for args, expected in [((), "main"), (("--ref", "v1.2.3"), "v1.2.3")]:
        result = subprocess.run(["bash", "-s", "--", *args], input=prefix,
                                capture_output=True, text=True, check=True, env=env)
        assert result.stdout.strip() == expected
        assert "Git branch" not in result.stderr

    master, slave = pty.openpty()
    try:
        def attach_tty():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        process = subprocess.Popen(["bash", "-s"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=slave, text=True, env=env, preexec_fn=attach_tty)
        os.close(slave)
        os.write(master, b"develop\n")
        output, _ = process.communicate(prefix, timeout=5)
        assert process.returncode == 0
        assert output.strip() == "develop"
    finally:
        os.close(master)


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
def test_failed_first_bootstrap_removes_only_new_runtime(tmp_path, data_state):
    root = tmp_path / "agent"
    if data_state == "existing":
        (root / "data").mkdir(parents=True)
        (root / "data/config.ini").write_text("original identity")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text('#!/bin/sh\nmkdir -p "$TEST_ROOT/tools/bin" "$TEST_ROOT/cache/uv"\n'
                    'if [ "$TEST_NEW_IDENTITY" = true ]; then\n'
                    '  mkdir -p "$TEST_ROOT/data"\n'
                    '  printf "new identity" > "$TEST_ROOT/data/config.ini"\n'
                    'fi\nexit 22\n')
    curl.chmod(0o755)

    script = Path(__file__).resolve().parents[3] / "scripts/install-drastic-agent.sh"
    bootstrap = (script.read_text()
                 .replace("/opt/drastic-agent", str(root))
                 .replace("/usr/local/bin/drastic-agent", str(tmp_path / "command"))
                 .replace("/etc/systemd/system/drastic-agent.service", str(tmp_path / "service"))
                 .replace('if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi', '"$@"')
                 .replace('if [ "$(id -u)" -ne 0 ]; then', 'if false; then')
                 .replace('systemctl is-active --quiet drastic-agent.service', 'false')
                 .replace('"$(stat -c %u "$root")" = 0', f'"$(stat -c %u "$root")" = {os.getuid()}'))
    result = subprocess.run(
        ["bash", "-s", "--", "--source", str(tmp_path / "unused"),
         *(["--reuse-data"] if data_state == "existing" else [])],
        input=bootstrap, text=True, capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "TEST_ROOT": str(root),
             "TEST_NEW_IDENTITY": str(data_state == "new").lower()},
    )

    assert result.returncode == 22
    if data_state != "none":
        assert (root / "data/config.ini").read_text() == (
            "original identity" if data_state == "existing" else "new identity"
        )
        assert (root / ".managed-by-drastic-agent").exists()
        assert {path.name for path in root.iterdir()} == {"data", ".managed-by-drastic-agent"}
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
        assert "--no-remove --no-install-recommends python3-guestfs libguestfs-tools python3-fuse python3-libnbd libnbd-bin" in calls
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
