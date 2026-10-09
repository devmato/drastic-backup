from datetime import date

from flask import make_response, send_file
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required, unset_jwt_cookies
from flask_smorest import Blueprint

from drastic_server.schemas.common import MessageSchema
from drastic_server.schemas.user import (
    NeedsInitResponseSchema,
    UserChangePasswordInputSchema,
    UserInitInputSchema,
    UserRecoveryExportInputSchema,
    UserResponseSchema,
)
from drastic_server.services import users
from drastic_server.views.api.errors import register_service_errors

blp = Blueprint("user", __name__, url_prefix="/api/user", description="User management")
register_service_errors(blp)


@blp.route("/")
class UserDetail(MethodView):
    @jwt_required()
    @blp.response(200, UserResponseSchema)
    def get(self):
        return users.get_user(get_jwt_identity())


@blp.route("/init")
class UserInit(MethodView):
    @blp.arguments(UserInitInputSchema)
    @blp.response(201, MessageSchema)
    def post(self, data):
        users.initialize_user(data)

        return {"msg": "User created successfully"}


@blp.route("/password")
class UserPassword(MethodView):
    @jwt_required()
    @blp.arguments(UserChangePasswordInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data):
        users.change_password(get_jwt_identity(), data)

        response = make_response({"msg": "Password changed. Sign in again."})
        unset_jwt_cookies(response)
        return response


@blp.route("/recovery-export")
class UserRecoveryExport(MethodView):
    @jwt_required()
    @blp.arguments(UserRecoveryExportInputSchema)
    def post(self, data):
        archive = users.recovery_archive(get_jwt_identity(), data["password"])

        response = send_file(
            archive,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"drastic-recovery-{date.today().isoformat()}.zip",
        )
        response.headers["Cache-Control"] = "no-store"
        return response


@blp.route("/needs_init")
class UserNeedsInit(MethodView):
    @blp.response(200, NeedsInitResponseSchema)
    def get(self):
        return {"needs_init": users.needs_initialization()}
