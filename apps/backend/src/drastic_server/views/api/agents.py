"""Agent HTTP contracts, including connection settings and operation history."""

from flask import current_app, jsonify
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort

from drastic_server.schemas.agent import (
    AgentInstallOptionsResponseSchema,
    AgentOperationDetailResponseSchema,
    AgentOperationQuerySchema,
    AgentOperationResponseSchema,
    AgentProxmoxSettingsInputSchema,
    AgentProxmoxSettingsResponseSchema,
    AgentProxmoxTestResponseSchema,
    AgentRegisterInputSchema,
    AgentRegisterResponseSchema,
    AgentRepositoryAssignInputSchema,
    AgentResponseSchema,
    AgentSyncInputSchema,
    AgentTrueNASSettingsInputSchema,
    AgentTrueNASSettingsResponseSchema,
    AgentTrueNASTestResponseSchema,
    AgentUpdateInputSchema,
    TrueNASDatasetsResponseSchema,
)
from drastic_server.schemas.common import MessageSchema
from drastic_server.services.agent import build_agent_install_targets, management
from drastic_server.services.agent.installer import installation_target
from drastic_server.services.exceptions import ResourceConflict
from drastic_server.services.operations import history
from drastic_server.utils.urls import explicit_public_url, public_server_url
from drastic_server.views.api.errors import register_service_errors

blp = Blueprint("agents", __name__, url_prefix="/api/agents", description="Agent operations")
register_service_errors(blp)


@blp.route("/installation-target")
class AgentInstallationTarget(MethodView):
    def get(self):
        # Unpaired installers need public build metadata, never a cached branch head.
        try:
            return jsonify(installation_target()), 200, {"Cache-Control": "no-store"}
        except ResourceConflict as exc:
            abort(409, message=str(exc), headers={"Cache-Control": "no-store"})


@blp.route("/register")
class AgentRegister(MethodView):
    @blp.arguments(AgentRegisterInputSchema)
    @blp.response(201, AgentRegisterResponseSchema)
    def post(self, data):
        return {**management.register_agent(data), "server_url": public_server_url()}


@blp.route("/install-options")
class AgentInstallOptions(MethodView):
    @jwt_required()
    @blp.response(200, AgentInstallOptionsResponseSchema)
    def get(self):
        return {"server_url": explicit_public_url(current_app.config),
                "targets": build_agent_install_targets(current_app.config)}


@blp.route("/")
class AgentList(MethodView):
    @jwt_required()
    @blp.response(200, AgentResponseSchema(many=True))
    def get(self):
        return management.list_agents(get_jwt_identity())


@blp.route("/<int:agent_id>")
class AgentDetail(MethodView):
    @jwt_required()
    @blp.response(200, AgentResponseSchema)
    def get(self, agent_id):
        return management.get_agent(get_jwt_identity(), agent_id)

    @jwt_required()
    @blp.arguments(AgentUpdateInputSchema)
    @blp.response(200, AgentResponseSchema)
    def put(self, data, agent_id):
        return management.update_agent(get_jwt_identity(), agent_id, data)

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, agent_id):
        management.delete_agent(get_jwt_identity(), agent_id)
        return {"msg": "Agent deleted"}


@blp.route("/<int:agent_id>/repositories")
class AgentRepositories(MethodView):
    @jwt_required()
    @blp.arguments(AgentRepositoryAssignInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data, agent_id):
        management.assign_repositories(get_jwt_identity(), agent_id, data)
        return {"msg": "Agent repositories updated"}


@blp.route("/<int:agent_id>/sync")
class AgentSync(MethodView):
    @jwt_required()
    @blp.arguments(AgentSyncInputSchema)
    @blp.response(200, MessageSchema)
    def post(self, data, agent_id):
        management.sync_agent(get_jwt_identity(), agent_id, data)
        return {"msg": "Agent synchronized"}


@blp.route("/<int:agent_id>/actions/<string:action>")
class AgentAction(MethodView):
    @jwt_required()
    @blp.response(200, MessageSchema)
    def post(self, agent_id, action):
        return management.run_action(get_jwt_identity(), agent_id, action)


def _connection_command(agent_id, command, data=None, **args):
    return management.connection_command(get_jwt_identity(), agent_id, command, data, **args)


@blp.route("/<int:agent_id>/proxmox-settings")
class AgentProxmoxSettings(MethodView):
    @jwt_required()
    @blp.response(200, AgentProxmoxSettingsResponseSchema)
    def get(self, agent_id):
        return _connection_command(agent_id, "get_proxmox_settings")

    @jwt_required()
    @blp.arguments(AgentProxmoxSettingsInputSchema)
    @blp.response(200, AgentProxmoxSettingsResponseSchema)
    def put(self, data, agent_id):
        return _connection_command(agent_id, "update_proxmox_settings", data)


@blp.route("/<int:agent_id>/proxmox-settings/test")
class AgentProxmoxSettingsTest(MethodView):
    @jwt_required()
    @blp.arguments(AgentProxmoxSettingsInputSchema)
    @blp.response(200, AgentProxmoxTestResponseSchema)
    def post(self, data, agent_id):
        return _connection_command(agent_id, "test_proxmox_settings", data)


@blp.route("/<int:agent_id>/connections/<string:kind>")
class AgentConnectionDetail(MethodView):
    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, agent_id, kind):
        if kind not in {"proxmox", "truenas"}:
            abort(404)
        _connection_command(agent_id, "delete_connection", kind=kind)
        return {"msg": "Connection removed"}


@blp.route("/<int:agent_id>/truenas-settings")
class AgentTrueNASSettings(MethodView):
    @jwt_required()
    @blp.response(200, AgentTrueNASSettingsResponseSchema)
    def get(self, agent_id):
        return _connection_command(agent_id, "truenas_settings")

    @jwt_required()
    @blp.arguments(AgentTrueNASSettingsInputSchema)
    @blp.response(200, AgentTrueNASSettingsResponseSchema)
    def put(self, data, agent_id):
        return _connection_command(agent_id, "truenas_settings", data, secret_field="api_key", action="save")


@blp.route("/<int:agent_id>/truenas-settings/test")
class AgentTrueNASSettingsTest(MethodView):
    @jwt_required()
    @blp.arguments(AgentTrueNASSettingsInputSchema)
    @blp.response(200, AgentTrueNASTestResponseSchema)
    def post(self, data, agent_id):
        return _connection_command(agent_id, "truenas_settings", data, secret_field="api_key", action="test")


@blp.route("/<int:agent_id>/truenas-datasets")
class AgentTrueNASDatasets(MethodView):
    @jwt_required()
    @blp.response(200, TrueNASDatasetsResponseSchema)
    def get(self, agent_id):
        return _connection_command(agent_id, "truenas_settings", action="datasets")


@blp.route("/<int:agent_id>/truenas-settings/cleanup")
class AgentTrueNASCleanup(MethodView):
    @jwt_required()
    @blp.response(200, AgentTrueNASSettingsResponseSchema)
    def post(self, agent_id):
        return _connection_command(agent_id, "truenas_settings", action="cleanup")


@blp.route("/<int:agent_id>/operations")
class AgentOperationList(MethodView):
    @jwt_required()
    @blp.arguments(AgentOperationQuerySchema, location="query")
    @blp.response(200, AgentOperationResponseSchema(many=True))
    def get(self, args, agent_id):
        return history.list_operations(get_jwt_identity(), agent_id, args)


@blp.route("/operations/<int:operation_id>")
class AgentOperationDetail(MethodView):
    @jwt_required()
    @blp.response(200, AgentOperationDetailResponseSchema)
    def get(self, operation_id):
        return history.get_operation(get_jwt_identity(), operation_id)

    @jwt_required()
    @blp.response(200, MessageSchema)
    def delete(self, operation_id):
        history.delete_operation(get_jwt_identity(), operation_id)
        return {"msg": "Operation deleted"}
