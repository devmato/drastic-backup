import pytest
import test_proxmox_settings_api

from drastic_common.secret_envelope import decrypt_with_private_key
from drastic_server.extensions import db
from drastic_server.integrations import agent as agent_command
from drastic_server.services.agent.request import AgentException, AgentRequestService
from drastic_server.services.jobs.configuration import ensure_job_connection

api_client = test_proxmox_settings_api.api_client

SETTINGS = {"api_url": "https://nas", "username": "backup", "verify_tls": True, "host_root": "/mnt/host"}


def test_truenas_settings_authorization_encryption_and_connection_status(api_client, monkeypatch):
    client, agent, foreign, private = api_client
    agent.protocol_version = 3
    db.session.commit()
    calls = []
    connections = {"truenas": {"configured": True, "available": True}}
    def call(_event, payload, **_kwargs):
        calls.append(payload)
        return {"type": "command", "state": "success", "logs": [], "data": {
            **SETTINGS, "api_key_configured": True, "configured": True, "connections": connections,
            "api_key": "must-not-be-returned",
        }}
    monkeypatch.setattr(agent_command, "call", call)
    url = f"/api/agents/{agent.id}/truenas-settings"
    assert client.application.test_client().get(url).status_code == 401
    assert client.put(f"/api/agents/{foreign.id}/truenas-settings", json=SETTINGS).status_code == 404
    response = client.put(url, json={**SETTINGS, "api_key": "secret"})
    assert response.status_code == 200
    assert "api_key" not in response.json
    assert agent.connections == connections
    assert calls[-1]["command"] == "truenas_settings"
    assert calls[-1]["args"]["action"] == "save"
    assert decrypt_with_private_key(calls[-1]["args"]["encrypted_value"], private) == "secret"
    assert "secret" not in str(calls[-1])
    assert client.put(url, json=SETTINGS).status_code == 200
    assert "encrypted_value" not in calls[-1]["args"]
    for patch in ({"api_url": "http://nas"}, {"api_url": "https://nas/api"}, {"host_root": "../mnt"}, {"api_key": "bad\nkey"}):
        assert client.put(url, json={**SETTINGS, **patch}).status_code == 422
    agent.protocol_version = 2
    db.session.commit()
    assert client.get(url).status_code == 400


def test_truenas_job_validation_and_agent_protocol(api_client):
    client, agent, _, _ = api_client
    with pytest.raises(ValueError, match="Update the agent"):
        ensure_job_connection(agent, "truenas")
    agent.protocol_version = 14
    agent.connections = {"truenas": {"configured": False, "available": True}}
    db.session.commit()
    config = {"paths": [{"dataset": "tank/data", "path": ".", "group": "dataset"}], "exclude_paths": [], "exclude_patterns": []}
    payload = {"agent_id": agent.id, "name": "NAS", "type": "truenas", "config": config}
    assert client.post("/api/jobs/", json=payload).status_code == 400
    # Use assignment because JSON columns do not track nested mutations.
    agent.connections = {"truenas": {"configured": True, "available": True}}
    db.session.commit()
    assert client.post("/api/jobs/", json={**payload, "config": {"paths": [{"dataset": "tank/../etc", "path": ".", "group": "dataset"}]}}).status_code == 422
    response = client.post("/api/jobs/", json=payload)
    assert response.status_code == 201
    job_id = response.json["id"]
    assert client.put(f"/api/jobs/{job_id}", json={"config": {"paths": []}}).status_code == 400
    assert client.get(f"/api/jobs/{job_id}").json["config"] == config
    agent.protocol_version = 13
    db.session.commit()
    assert client.put(f"/api/jobs/{job_id}", json={"config": config}).status_code == 400
    with pytest.raises(AgentException, match="protocol 14"):
        AgentRequestService(agent).sync()


def test_path_selection_validation_and_round_trip(api_client):
    client, agent, _, _ = api_client
    agent.protocol_version = 16
    agent.connections = {"truenas": {"configured": True, "available": True}}
    db.session.commit()
    root = {"dataset": "tank", "path": ".", "group": "dataset"}
    folder = {"dataset": "tank", "path": "photos", "group": "folder"}
    excluded = {"dataset": "tank", "path": "photos/private", "group": "folder"}
    config = {"paths": [folder], "exclude_paths": [root, excluded], "exclude_patterns": ["*.tmp"]}
    payload = {"agent_id": agent.id, "name": "NAS", "type": "truenas", "config": config}
    response = client.post("/api/jobs/", json=payload)
    assert response.status_code == 201
    job_id = response.json["id"]
    assert client.put(f"/api/jobs/{job_id}", json={"config": config}).status_code == 200
    assert client.get(f"/api/jobs/{job_id}").json["config"] == config
    assert AgentRequestService(agent).sync()["jobs"][0]["config"] == config
    agent.protocol_version = 15
    db.session.commit()
    with pytest.raises(ValueError, match="protocol 16"):
        ensure_job_connection(agent, "truenas", config)
    with pytest.raises(AgentException, match="protocol 16"):
        AgentRequestService(agent).sync()
    assert client.put(f"/api/jobs/{job_id}", json={"config": config}).status_code == 400
    assert client.put(f"/api/jobs/{job_id}", json={"config": {"paths": [{"path": "docs"}]}}).status_code == 400
    agent.protocol_version = 16
    db.session.commit()
    invalid_configs = [
        {**config, "exclude_paths": entries} for entries in ([folder], [excluded, excluded])
    ] + [{**config, "paths": [{**folder, "path": path}]} for path in ("", "/etc", "../etc", "a/../b", ".zfs/snapshot", "a//b", "a/./b", "a\x00b")]
    for invalid in invalid_configs:
        assert client.post("/api/jobs/", json={**payload, "config": invalid}).status_code == 422
        assert client.put(f"/api/jobs/{job_id}", json={"config": invalid}).status_code == 400
        assert client.get(f"/api/jobs/{job_id}").json["config"] == config
