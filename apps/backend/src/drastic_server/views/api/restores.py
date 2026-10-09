from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort

from drastic_server.schemas.common import MessageSchema
from drastic_server.schemas.restore import (
    ProxmoxRestoreInputSchema,
    RestoreEntriesQuerySchema,
    RestoreEntriesResponseSchema,
    RestoreOptionsQuerySchema,
    RestoreOptionsResponseSchema,
    RestoreSnapshotsQuerySchema,
    RestoreSnapshotsResponseSchema,
    RestoreStartInputSchema,
    RestoreStartResponseSchema,
)
from drastic_server.services.exceptions import RestoreServiceException
from drastic_server.services.restore import RestoreService
from drastic_server.views.api.errors import register_service_errors

blp = Blueprint("restores", __name__, url_prefix="/api/restores", description="Restore operations")
register_service_errors(blp)


def _handle_restore_error(exc):
    status_code = 504 if isinstance(exc, TimeoutError) else 400
    if getattr(exc, "conflict", False):
        status_code = 409
    abort(status_code, message=str(exc))


@blp.route("/options")
class RestoreOptions(MethodView):
    @jwt_required()
    @blp.arguments(RestoreOptionsQuerySchema, location="query")
    @blp.response(200, RestoreOptionsResponseSchema)
    def get(self, args):
        return RestoreService.get_options(int(get_jwt_identity()), args["job_id"])


@blp.route("/snapshots")
class RestoreSnapshots(MethodView):
    @jwt_required()
    @blp.arguments(RestoreSnapshotsQuerySchema, location="query")
    @blp.response(200, RestoreSnapshotsResponseSchema)
    def get(self, args):
        try:
            return RestoreService.list_snapshots(int(get_jwt_identity()), **args)
        except (RestoreServiceException, TimeoutError) as exc:
            _handle_restore_error(exc)


@blp.route("/proxmox")
class ProxmoxRestore(MethodView):
    @jwt_required()
    @blp.arguments(ProxmoxRestoreInputSchema)
    def post(self, data):
        try:
            return RestoreService.proxmox_action(int(get_jwt_identity()), data)
        except (RestoreServiceException, TimeoutError) as exc:
            _handle_restore_error(exc)


@blp.route("/entries")
class RestoreEntries(MethodView):
    @jwt_required()
    @blp.arguments(RestoreEntriesQuerySchema, location="query")
    @blp.response(200, RestoreEntriesResponseSchema)
    def get(self, args):
        try:
            return RestoreService.list_entries(int(get_jwt_identity()), **args)
        except (RestoreServiceException, TimeoutError) as exc:
            _handle_restore_error(exc)


@blp.route("/")
class RestoreStart(MethodView):
    @jwt_required()
    @blp.arguments(RestoreStartInputSchema)
    @blp.response(202, RestoreStartResponseSchema)
    def post(self, data):
        try:
            return RestoreService.start_restore(int(get_jwt_identity()), data)
        except (RestoreServiceException, TimeoutError) as exc:
            _handle_restore_error(exc)


@blp.route("/<int:operation_id>/cancel")
class RestoreCancel(MethodView):
    @jwt_required()
    @blp.response(200, MessageSchema)
    def post(self, operation_id):
        try:
            return RestoreService.cancel_restore(int(get_jwt_identity()), operation_id)
        except (RestoreServiceException, TimeoutError) as exc:
            _handle_restore_error(exc)
