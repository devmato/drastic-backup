import configparser
import json
from types import SimpleNamespace

import dataset
import pytest

import drastic_agent.agent.agent as agent_module
from drastic_agent.agent.agent import Agent, _encode_config_secret, _redact_secrets
from drastic_agent.agent.enums import AgentReportState
from drastic_agent.proxmox import ProxmoxError
from drastic_common.secret_envelope import encrypt_for_public_key, generate_agent_keypair

SETTINGS = {
    "api_url": "https://127.0.0.1:8006/api2/json",
    "token_id": "root@pam!drastic-agent",
    "node": "",
    "verify_tls": False,
}


@pytest.fixture(scope="module")
def keys():
    return generate_agent_keypair()


@pytest.fixture
def agent(monkeypatch, tmp_path, keys):
    test_db = dataset.connect(f"sqlite:///{tmp_path}/database.db")
    monkeypatch.setattr(agent_module, "db", test_db)
    monkeypatch.setattr(agent_module, "agent_settings", test_db["agent"])
    for name in ("API_URL", "TOKEN_ID", "TOKEN_SECRET", "NODE", "VERIFY_TLS"):
        monkeypatch.delenv(f"DRASTIC_PROXMOX_{name}", raising=False)
    instance = Agent.__new__(Agent)
    instance._Agent__config = configparser.ConfigParser()
    instance._Agent__config["AGENT"] = {
        "private_key": _encode_config_secret(keys[0]),
        "public_key": _encode_config_secret(keys[1]),
    }
    yield instance
    test_db.engine.dispose()


def test_saved_settings_survive_restart_override_environment_and_never_return_secret(agent, monkeypatch):
    monkeypatch.setenv("DRASTIC_PROXMOX_TOKEN_ID", SETTINGS["token_id"])
    monkeypatch.setenv("DRASTIC_PROXMOX_TOKEN_SECRET", "legacy-secret")
    assert agent.get_proxmox_client().token_secret == "legacy-secret"
    assert agent.cmd_get_proxmox_settings().data["source"] == "environment"

    response = agent.cmd_update_proxmox_settings(
        SETTINGS, encrypt_for_public_key("saved-secret", agent.public_key)
    )
    assert response.state == AgentReportState.success
    assert response.data["source"] == "agent"
    assert response.data["token_secret_configured"] is True
    assert "saved-secret" not in json.dumps(response.data)
    saved = agent_module.agent_settings.find_one(name="proxmox")
    assert "saved-secret" not in saved["settings"]
    assert "token_secret" not in json.loads(saved["settings"])

    restarted = Agent.__new__(Agent)
    restarted._Agent__config = agent._Agent__config
    assert restarted.get_proxmox_client().token_secret == "saved-secret"
    assert restarted.get_proxmox_client().verify_tls is False

    # Omitting the secret keeps the saved token, not the environment token.
    response = restarted.cmd_update_proxmox_settings({**SETTINGS, "verify_tls": True})
    assert response.state == AgentReportState.success
    assert restarted.get_proxmox_client().token_secret == "saved-secret"
    assert restarted.get_proxmox_client().verify_tls is True


def test_test_and_discovery_use_effective_settings_without_saving(agent, monkeypatch):
    monkeypatch.setenv("DRASTIC_PROXMOX_TOKEN_ID", SETTINGS["token_id"])
    monkeypatch.setenv("DRASTIC_PROXMOX_TOKEN_SECRET", "legacy-secret")
    monkeypatch.setattr(agent_module, "ensure_vzdump_available", lambda: None)
    clients = []

    def guests(api):
        clients.append(api)
        api._node = "pve01"
        return [{"vmid": 101}]

    monkeypatch.setattr(
        agent_module, "get_proxmox_guest_driver", lambda: SimpleNamespace(list_supported_guests=guests)
    )
    response = agent.cmd_test_proxmox_settings(
        SETTINGS, encrypt_for_public_key("unsaved-secret", agent.public_key)
    )
    assert response.state == AgentReportState.success
    assert response.data == {"node": "pve01", "guest_count": 1}
    assert agent_module.agent_settings.find_one(name="proxmox") is None
    assert agent.get_proxmox_client().token_secret == "legacy-secret"
    assert clients[-1].token_secret == "unsaved-secret"

    agent.cmd_update_proxmox_settings(
        SETTINGS, encrypt_for_public_key("saved-secret", agent.public_key)
    )
    assert agent.cmd_get_proxmox_guests().data == {"guests": [{"vmid": 101}]}
    assert clients[-1].token_secret == "saved-secret"


def test_rejected_updates_and_failed_writes_preserve_previous_settings(agent):
    agent.cmd_update_proxmox_settings(
        SETTINGS, encrypt_for_public_key("saved-secret", agent.public_key)
    )
    changed = {**SETTINGS, "api_url": "https://other-host:8006/api2/json"}
    assert agent.cmd_update_proxmox_settings(changed).state == AgentReportState.failed
    for envelope in ({"v": 0}, {"v": "invalid"}, {"v": 1, "alg": []}):
        assert agent.cmd_update_proxmox_settings(SETTINGS, envelope).state == AgentReportState.failed
    agent_module.db.query(
        "CREATE TRIGGER reject_settings BEFORE UPDATE ON agent "
        "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
    )
    response = agent.cmd_update_proxmox_settings(
        SETTINGS, encrypt_for_public_key("replacement-secret", agent.public_key)
    )
    assert response.state == AgentReportState.failed
    assert agent.get_proxmox_client().token_secret == "saved-secret"

    agent_module.agent_settings.delete(name="proxmox")
    agent_module.agent_settings.insert({"name": "proxmox", "settings": "corrupt"})
    with pytest.raises(ProxmoxError, match="could not be loaded"):
        agent.get_proxmox_client()


def test_connection_test_checks_host_tools_and_redacts_errors(agent, monkeypatch):
    envelope = encrypt_for_public_key("test-secret", agent.public_key)

    def unavailable():
        raise ProxmoxError("vzdump is not available")

    monkeypatch.setattr(agent_module, "ensure_vzdump_available", unavailable)
    assert agent.cmd_test_proxmox_settings(SETTINGS, envelope).state == AgentReportState.failed

    monkeypatch.setattr(agent_module, "ensure_vzdump_available", lambda: None)

    def fail(api):
        raise ProxmoxError(f"API rejected {api.token_secret}")

    monkeypatch.setattr(
        agent_module, "get_proxmox_guest_driver", lambda: SimpleNamespace(list_supported_guests=fail)
    )
    report = agent.cmd_test_proxmox_settings(SETTINGS, envelope)
    assert report.state == AgentReportState.failed
    assert "test-secret" not in str(report.logs)
    assert _redact_secrets({"token_secret": "test-secret", "encrypted_value": envelope}) == {
        "token_secret": "<redacted>", "encrypted_value": "<redacted>"
    }
