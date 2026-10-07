import pytest
import test_proxmox_settings_api

from drastic_common.secret_envelope import decrypt_with_private_key
from drastic_server.extensions import db
from drastic_server.services.agent import command as agent_command
from drastic_server.services.agent.request import AgentException, AgentRequestService
from drastic_server.services.job import ensure_job_connection

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


def test_truenas_job_validation_preserves_existing_jobs_and_rejects_unready_agent(api_client):
    client, agent, _, _ = api_client
    with pytest.raises(ValueError, match="Update the agent"):
        ensure_job_connection(agent, "truenas")
    agent.protocol_version = 3
    agent.connections = {"truenas": {"configured": False, "available": True}}
    db.session.commit()
    payload = {"agent_id": agent.id, "name": "NAS", "type": "truenas", "config": {"datasets": ["tank/data"]}}
    assert client.post("/api/jobs/", json=payload).status_code == 400
    # Use assignment because JSON columns do not track nested mutations.
    agent.connections = {"truenas": {"configured": True, "available": True}}
    db.session.commit()
    assert client.post("/api/jobs/", json={**payload, "config": {"datasets": ["tank/../etc"]}}).status_code == 422
    response = client.post("/api/jobs/", json=payload)
    assert response.status_code == 201
    job_id = response.json["id"]
    assert client.put(f"/api/jobs/{job_id}", json={"config": {"datasets": []}}).status_code == 400
    assert client.get(f"/api/jobs/{job_id}").json["config"]["datasets"] == ["tank/data"]
    assert client.get(f"/api/jobs/{job_id}").json["config"]["include_children"] is False
    config = {"datasets": ["tank"], "include_children": True}
    assert client.put(f"/api/jobs/{job_id}", json={"config": config}).status_code == 200
    assert client.get(f"/api/jobs/{job_id}").json["config"]["include_children"] is True
    response = client.post("/api/jobs/", json={**payload, "config": config})
    assert response.status_code == 201
    assert client.get(f"/api/jobs/{response.json['id']}").json["config"]["include_children"] is True
    assert client.post("/api/jobs/", json={**payload, "config": {**config, "include_children": "invalid"}}).status_code == 422


def test_dataset_exclusions_validate_round_trip_and_require_capable_agents(api_client):
    client, agent, _, _ = api_client
    agent.protocol_version = 12
    agent.connections = {"truenas": {"configured": True, "available": True}}
    db.session.commit()
    config = {"datasets": ["tank"], "include_children": True, "exclude_datasets": ["tank/data"]}
    payload = {"agent_id": agent.id, "name": "NAS", "type": "truenas", "config": config}
    response = client.post("/api/jobs/", json=payload)
    assert response.status_code == 400
    assert "protocol 13" in response.json["message"]
    response = client.post("/api/jobs/", json={**payload, "config": {**config, "exclude_datasets": []}})
    assert response.status_code == 201
    job_id = response.json["id"]
    assert "exclude_datasets" not in client.get(f"/api/jobs/{job_id}").json["config"]
    assert "exclude_datasets" not in AgentRequestService(agent).sync()["jobs"][0]["config"]
    assert client.put(f"/api/jobs/{job_id}", json={"config": config}).status_code == 400

    agent.protocol_version = 13
    db.session.commit()
    assert client.put(f"/api/jobs/{job_id}", json={"config": config}).status_code == 200
    assert client.get(f"/api/jobs/{job_id}").json["config"]["exclude_datasets"] == ["tank/data"]
    assert AgentRequestService(agent).sync()["jobs"][0]["config"]["exclude_datasets"] == ["tank/data"]
    for excluded in (["tank/../data"], ["tank/data", "tank/data"], ["tank"], ["tank", "tank/data"]):
        invalid = {**config, "exclude_datasets": excluded}
        assert client.post("/api/jobs/", json={**payload, "config": invalid}).status_code == 422
        assert client.put(f"/api/jobs/{job_id}", json={"config": invalid}).status_code == 400
        assert client.get(f"/api/jobs/{job_id}").json["config"]["exclude_datasets"] == ["tank/data"]

    agent.protocol_version = 12
    db.session.commit()
    with pytest.raises(AgentException, match="protocol 13"):
        AgentRequestService(agent).sync()
