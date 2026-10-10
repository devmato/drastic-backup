import subprocess
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_smorest import Api

from drastic_server.app import _register_install_route
from drastic_server.models.agent import AgentOperationState
from drastic_server.services.agent import installer, management
from drastic_server.services.agent.installer import (
    build_agent_install_targets,
    render_linux_agent_install_script,
)
from drastic_server.services.exceptions import ResourceConflict
from drastic_server.views.api.agents import blp


def test_pipe_installer_quotes_configuration_and_preserves_arguments():
    script = render_linux_agent_install_script(
        "https://backup.example.net/a'b", "https://example.net/team/backup.git"
    )
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    # Exit before any bootstrap side effects, after reading the generated defaults.
    prefix = script.split("ROOT=/opt/drastic-agent", 1)[0]
    result = subprocess.run(
        ["bash", "-c", prefix + 'printf "%s\\n" "$DRASTIC_SERVER" "$DRASTIC_AGENT_GIT_REPOSITORY"'],
        text=True, capture_output=True, check=True,
    )
    assert result.stdout.splitlines() == [
        "https://backup.example.net/a'b", "https://example.net/team/backup.git"
    ]


def test_docker_targets_keep_both_architectures_and_configured_image():
    targets = build_agent_install_targets({"AGENT_IMAGE": "registry.example/agent:v1.2.3"})
    assert {target["arch"] for target in targets} == {"amd64", "arm64"}
    assert all(target["image"] == "registry.example/agent:v1.2.3" for target in targets)


def test_public_bootstrap_controller_is_current_and_uncached():
    app = Flask(__name__)
    _register_install_route(app)
    response = app.test_client().get("/install.py")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.mimetype == "text/x-python"
    script = response.get_data(as_text=True)
    assert "\nINDEPENDENT_LIFECYCLE = True\n" in script
    compile(script, "downloaded-installer.py", "exec")


@pytest.mark.parametrize("revision,version,status", [
    ("a" * 40, "2026-10-10-aaaaaaaa", 200),
    ("a" * 40, "2026-10-10-aaaaaaaa-dirty", 409),
    ("a" * 40, "2026-10-10-bbbbbbbb", 409),
    (None, "2026-10-10-aaaaaaaa", 409),
    (None, "unknown", 409),
])
def test_public_installation_target_requires_reproducible_backend(monkeypatch, revision, version, status):
    app = Flask(__name__)
    app.config.update(API_TITLE="Test", API_VERSION="1", OPENAPI_VERSION="3.0.3")
    Api(app).register_blueprint(blp)
    monkeypatch.setattr(installer, "get_revision", lambda: revision)
    monkeypatch.setattr(installer, "get_version", lambda: version)
    response = app.test_client().get("/api/agents/installation-target")
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    if status == 200:
        assert response.json == {"revision": revision, "version": version}
    else:
        assert "no reproducible agent installation target" in response.json["message"]


def test_web_update_rejects_dirty_backend_before_remote_dispatch(monkeypatch):
    agent = SimpleNamespace(online=True, os="linux", install_type="git", protocol_version=15)
    monkeypatch.setattr(management, "get_agent", lambda user_id, agent_id: agent)
    monkeypatch.setattr(installer, "get_revision", lambda: "a" * 40)
    monkeypatch.setattr(installer, "get_version", lambda: "2026-10-10-aaaaaaaa-dirty")
    calls = []

    def send_command(agent, command):
        calls.append(command)
        return {"state": AgentOperationState.success}

    monkeypatch.setattr(management.AgentService, "send_command", send_command)
    with pytest.raises(ResourceConflict):
        management.run_action(1, 2, "update")
    assert calls == []
    monkeypatch.setattr(installer, "get_version", lambda: "2026-10-10-aaaaaaaa")
    assert management.run_action(1, 2, "update") == {"msg": "Agent update started"}
    assert calls == ["update"]
