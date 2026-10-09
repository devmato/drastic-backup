"""Socket transport; callbacks signal work without blocking connection recovery."""

import logging

import socketio

from drastic_agent.agent.schemas import AgentReportSchema
from drastic_common import diagnostics


def create_client(*, execute, sync_pending, connect_requested):
    client = socketio.Client(logger=False, engineio_logger=False)

    @client.event(namespace="/agent")
    def connect():
        sync_pending.set()
        logging.info("Connected to server")
        diagnostics.record("connection.connected", {})

    @client.event(namespace="/agent")
    def disconnect(reason):
        if reason == client.reason.SERVER_DISCONNECT:
            connect_requested.set()
        logging.warning("Disconnected from server: %s", reason)
        diagnostics.record("connection.disconnected", {"reason": reason})

    @client.event(namespace="/agent")
    def connect_error(data=None):
        logging.warning("Agent connection failed: %s", data)

    @client.on("execute", namespace="/agent")
    def on_execute(data=None):
        return AgentReportSchema().dump(execute(data or {}))

    return client


def request(client, action, args, *, timeout):
    if client and client.connected:
        try:
            return client.call("request", {"action": action, "args": args}, namespace="/agent", timeout=timeout)
        except Exception as exc:
            # The timed-out request may predate a successful reconnect. Do not tear it down.
            logging.warning("Agent request '%s' failed (%s): %s", action, type(exc).__name__, exc)
    return {"success": False, "result": {}}
