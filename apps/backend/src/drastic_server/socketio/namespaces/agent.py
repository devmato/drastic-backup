"""Socket.IO boundary for agent sessions and authenticated requests."""

from flask import current_app, request
from flask_socketio import Namespace

from drastic_common.diagnostics import redact
from drastic_server.extensions import db, socketio
from drastic_server.services.agent import AgentRequestService
from drastic_server.services.agent.sessions import connect_agent, disconnect_agent
from drastic_server.services.diagnostics import record


class AgentNamespace(Namespace):
    def on_connect(self, auth):
        if not connect_agent(auth, request.sid):
            current_app.logger.info("Agent connection rejected from %s", request.remote_addr)
            return False
        current_app.logger.info("Agent connected from %s", request.remote_addr)

    def on_disconnect(self):
        disconnect_agent(request.sid)

    def on_request(self, data):
        agent_request = None
        try:
            agent_request = AgentRequestService.get_by_sid(request.sid)
            return {"success": True, "result": getattr(agent_request, data["action"])(**data["args"])}
        except Exception as exc:
            # Like the HTTP boundary, a failed socket request must discard unfinished work.
            db.session.rollback()
            if agent_request is not None:
                record(agent_request.agent.user_id, "agent.request_failed", {
                    "action": data.get("action") if isinstance(data, dict) else None,
                    "error": type(exc).__name__, "message": redact(str(exc)),
                }, agent_id=agent_request.agent.id)
            current_app.logger.warning("Agent request failed: %s", type(exc).__name__)
            return {"success": False, "result": {"error": type(exc).__name__}}


agentnamespace = AgentNamespace("/agent")
socketio.on_namespace(agentnamespace)
