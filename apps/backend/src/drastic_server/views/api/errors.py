"""Translate application failures at the HTTP boundary."""

from http import HTTPStatus

from marshmallow import ValidationError

from drastic_server.extensions import db
from drastic_server.integrations.agent import is_agent_conflict_response, is_agent_timeout_response
from drastic_server.services.exceptions import (
    AgentCommandFailed,
    AuthenticationFailed,
    InvalidInput,
    ResourceConflict,
    ResourceNotFound,
)
from drastic_server.services.repository import RepositorySecretError


def _response(status, message, *, errors=None):
    db.session.rollback()
    payload = {"code": status, "status": HTTPStatus(status).phrase, "message": message}
    if errors is not None:
        payload["errors"] = errors
    return payload, status


def register_service_errors(blueprint):
    """Preserve API error payloads while allowing services to run outside requests."""
    @blueprint.errorhandler(ResourceNotFound)
    def not_found(exc):
        return _response(404, str(exc))

    @blueprint.errorhandler(InvalidInput)
    def invalid_input(exc):
        return _response(422, str(exc))

    @blueprint.errorhandler(AuthenticationFailed)
    def authentication_failed(exc):
        return _response(401, str(exc))

    @blueprint.errorhandler(ResourceConflict)
    def conflict(exc):
        return _response(409, str(exc))

    @blueprint.errorhandler(RepositorySecretError)
    def recovery_key(exc):
        return _response(401, str(exc), errors={"recovery_key": ["required"]})

    @blueprint.errorhandler(ValueError)
    def invalid_configuration(exc):
        return _response(400, str(exc))

    @blueprint.errorhandler(ValidationError)
    def invalid_payload(exc):
        return _response(422, "Unprocessable Entity", errors=exc.messages)

    @blueprint.errorhandler(AgentCommandFailed)
    def agent_command(exc):
        status = 504 if is_agent_timeout_response(exc.response) else 400
        if is_agent_conflict_response(exc.response):
            status = 409
        return _response(status, str(exc))
