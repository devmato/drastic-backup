import json
from datetime import timedelta
from uuid import uuid4

import pytest
from flask_jwt_extended import create_access_token

from drastic_server.app import create_app
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentOperation, AgentOperationLog
from drastic_server.models.diagnostic import DiagnosticEvent
from drastic_server.models.job import Job, JobAction, JobSchedule
from drastic_server.models.repository import Repository
from drastic_server.models.retention import Retention
from drastic_server.models.user import User
from drastic_server.services import diagnostics
from drastic_server.services.agent.request import AgentRequestService


@pytest.fixture
def context(monkeypatch, tmp_path):
    monkeypatch.setenv("DRASTIC_ENV", "test")
    monkeypatch.setenv("DRASTIC_APP_MASTER_SECRET", "diagnostic-test-master-secret")
    monkeypatch.setenv("DRASTIC_SQLALCHEMY_DATABASE_URI", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DRASTIC_JWT_COOKIE_CSRF_PROTECT", "false")
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        users = [User(name=name, password="unused", encrypted_recovery_key={}) for name in ("owner", "other")]
        agents = [Agent(user=user, secret="unused", protocol_version=5) for user in users]
        operations = [AgentOperation(agent=agent, type="backup", state="running", source="manual",
                                    data={"password": "must-not-leak", "backup_phase": "backup"}) for agent in agents]
        db.session.add_all(users + agents + operations)
        db.session.flush()
        db.session.add(AgentOperationLog(operation=operations[0], sequence=1, level="info",
                                        message="Failed https://user:password@example.test/path?token=hidden"))
        db.session.commit()
        client = app.test_client()
        client.set_cookie("access_token_cookie", create_access_token(identity=str(users[0].id)))
        try:
            yield client, users, agents, operations
        finally:
            db.session.remove()
            db.drop_all()


def rpc(client, token, method="tools/call", params=None, **headers):
    return client.post("/mcp/debug", headers={"Authorization": f"Bearer {token}", **headers},
                       json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def tool(client, token, name, **args):
    response = rpc(client, token, params={"name": name, "arguments": args})
    assert response.status_code == 200, response.data
    result = response.json["result"]
    return json.loads(result["content"][0]["text"])


def test_opt_in_read_only_owner_scope_and_revocation(context):
    client, users, agents, operations = context
    second_agent = Agent(user=users[0], secret="unused", protocol_version=5)
    db.session.add(second_agent)
    db.session.commit()
    heartbeat = {"type": "command", "state": "running", "data": {"diagnostic": True}}
    assert client.application.test_client().post("/api/user/debug").status_code == 401
    assert client.get("/api/user/debug").json["enabled"] is False
    diagnostics.record(users[0].id, "test", {"x": 1})
    assert DiagnosticEvent.query.count() == 0
    response = client.post("/api/user/debug")
    token = response.json["token"]
    assert "agents" not in response.json
    for agent in (agents[0], second_agent):
        assert AgentRequestService(agent).operation(heartbeat)["enabled"] is True
    assert AgentRequestService(agents[1]).operation(heartbeat)["enabled"] is False
    assert {agent["id"] for agent in tool(client, token, "debug_context")["agents"]} == {agents[0].id, second_agent.id}
    assert token not in str(client.get("/api/user/debug").json)
    assert token != db.session.get(User, users[0].id).debug_token_hash
    assert response.headers["Cache-Control"] == "no-store"
    assert rpc(client, "wrong").status_code == 401
    assert rpc(client, token, Origin="https://evil.test").status_code == 403
    assert rpc(client, token, **{"MCP-Protocol-Version": "unsupported"}).status_code == 400
    assert rpc(client, token, "initialize").json["result"]["capabilities"] == {"tools": {}}
    assert all(item["annotations"]["readOnlyHint"] for item in rpc(client, token, "tools/list").json["result"]["tools"])
    assert [item["id"] for item in tool(client, token, "list_operations")["items"]] == [operations[0].id]
    assert tool(client, token, "inspect_operation", operation_id=operations[1].id) == {"error": "Not found"}
    assert tool(client, token, "inspect_agent", agent_id=agents[1].id) == {"error": "Not found"}
    assert "must-not-leak" not in str(tool(client, token, "inspect_operation", operation_id=operations[0].id))
    assert "hidden" not in str(tool(client, token, "get_operation_logs", operation_id=operations[0].id))
    assert rpc(client, token, params={"name": "list_operations", "arguments": {"limit": 101}}).json["error"]["code"] == -32602
    assert rpc(client, token, params={"name": "delete_repository"}).json["error"]["code"] == -32602
    new_token = client.post("/api/user/debug").json["token"]
    assert rpc(client, token).status_code == 401
    assert rpc(client, new_token, "ping").json["result"] == {}
    client.delete("/api/user/debug")
    assert rpc(client, new_token).status_code == 401
    for agent in (agents[0], second_agent):
        assert AgentRequestService(agent).operation(heartbeat)["enabled"] is False


def test_agent_events_pagination_redaction_dedup_and_retention(context, monkeypatch):
    client, users, agents, operations = context
    token = client.post("/api/user/debug").json["token"]
    report = {"uuid": str(uuid4()), "type": "command", "state": "running", "data": {"diagnostic": True},
              "logs": [{"sequence": 1, "message": "process.started", "created": diagnostics.utcnow().isoformat() + "Z",
                        "data": {"operation_uuid": operations[0].uuid, "payload": {"password": "private", "pid": 123}}}]}
    service = AgentRequestService(agents[0])
    assert service.operation(report)["enabled"] is True
    service.operation(report)
    assert DiagnosticEvent.query.count() == 1
    assert AgentOperation.query.count() == 2  # Internal telemetry does not create user operations.
    assert "private" not in str(DiagnosticEvent.query.first().payload)
    page = tool(client, token, "get_diagnostics", operation_id=operations[0].id, limit=1)
    assert page["items"][0]["payload"]["pid"] == 123
    detail = tool(client, token, "get_diagnostic_event", event_id=page["items"][0]["id"])
    assert detail["payload"]["pid"] == 123
    assert tool(client, token, "get_diagnostics", after_id=page["next_after_id"])["items"] == []
    with pytest.raises(ValueError):
        service.operation(dict(report, uuid="bad"))
    original_record = diagnostics.record
    monkeypatch.setattr(diagnostics, "record", lambda *_args, **_kwargs: False)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        service.operation(report)
    monkeypatch.setattr(diagnostics, "record", original_record)
    old = DiagnosticEvent.query.first()
    old.received_at = diagnostics.utcnow() - timedelta(days=15)
    db.session.commit()
    assert tool(client, token, "get_diagnostics")["items"] == []
    diagnostics.cleanup()
    assert DiagnosticEvent.query.count() == 0
    monkeypatch.setattr(diagnostics, "MAX_USER_EVENTS", 2)
    for number in range(3):
        diagnostics.record(users[0].id, "counter", {"number": number})
    diagnostics.cleanup()
    assert DiagnosticEvent.query.count() == 2
    client.delete("/api/user/debug")
    service.operation(dict(report, uuid=str(uuid4())))
    assert DiagnosticEvent.query.count() == 2


def test_operation_snapshots_use_source_timestamp_and_existing_report(context):
    client, _, agents, operations = context
    client.post("/api/user/debug")
    sampled_at = diagnostics.utcnow() - timedelta(seconds=2)
    report = {"uuid": operations[0].uuid, "type": "backup", "state": "running",
              "data": {"bytes_processed": 100}, "diagnostic_at": sampled_at.isoformat() + "Z"}
    service = AgentRequestService(agents[0])
    service.operation(report)
    service.operation(report)  # Lost acknowledgement: the same sample is not appended twice.
    service.operation({key: value for key, value in report.items() if key != "diagnostic_at"})
    event = DiagnosticEvent.query.one()
    assert event.event_type == "operation.received"
    assert event.occurred_at == sampled_at < event.received_at
    assert event.payload["data"]["bytes_processed"] == 100


def test_configuration_schemas_keep_debug_fields_and_exclude_credentials(context):
    client, users, agents, _ = context
    repository = Repository(user=users[0], name="Repo", kind="custom", location="sftp:user:hidden@host:/backup")
    job = Job(agent=agents[0], name="Files", type="file")
    policy = Retention(user=users[0], name="Keep three", keep_last=3)
    schedule = JobSchedule(job=job, repository=repository, retention=policy, enabled=True, cron_string="* * * * *")
    action = JobAction(job=job, hook="start", module="command", data={"command": "must-not-export"})
    db.session.add_all([repository, job, policy, schedule, action])
    db.session.commit()
    token = client.post("/api/user/debug").json["token"]
    job_data = tool(client, token, "inspect_configuration", kind="job", id=job.id)
    assert job_data["schedule_ids"] == [schedule.id]
    assert job_data["actions"] == [{"hook": "start", "module": "command"}]
    repo_data = tool(client, token, "inspect_configuration", kind="repository", id=repository.id)
    assert "hidden" not in repo_data["location"] and "environment" not in repo_data
    assert tool(client, token, "inspect_configuration", kind="schedule", id=schedule.id)["cron"] == "* * * * *"
    assert tool(client, token, "inspect_configuration", kind="retention", id=policy.id)["keep_last"] == 3


def test_browser_diagnostics_reject_foreign_operations_and_disabled_recording(context):
    client, _, _, operations = context
    payload = {"event_type": "operation.loaded", "operation_id": operations[0].id}
    assert client.post("/api/user/debug/browser", json=payload).status_code == 204
    assert DiagnosticEvent.query.count() == 0
    client.post("/api/user/debug")
    assert client.post("/api/user/debug/browser", json={**payload, "operation_id": operations[1].id}).status_code == 404
    assert client.post("/api/user/debug/browser", json={**payload, "raw_body": "not permitted"}).status_code == 400
    assert client.post("/api/user/debug/browser", json=payload).status_code == 204
    assert DiagnosticEvent.query.first().component == "frontend"


def test_official_mcp_client(context):
    """Run with `uv run --with mcp --with httpx python -m pytest tests/test_debug_access.py`."""
    import asyncio
    from threading import Thread

    from werkzeug.serving import make_server

    mcp = pytest.importorskip("mcp")
    httpx = pytest.importorskip("httpx")
    from mcp.client.streamable_http import streamable_http_client

    client, _, _, _ = context
    token = client.post("/api/user/debug").json["token"]
    server = make_server("127.0.0.1", 0, client.application, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def check():
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
            async with streamable_http_client(f"http://127.0.0.1:{server.server_port}/mcp/debug", http_client=http) as streams:
                read, write, *_ = streams
                async with mcp.ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert any(tool.name == "debug_context" for tool in tools.tools)
                    result = await session.call_tool("debug_context", {})
                    assert result.model_dump(by_alias=True)["isError"] is False
                    assert json.loads(result.content[0].text)["enabled"] is True

    try:
        asyncio.run(check())
    finally:
        server.shutdown()
        thread.join()
