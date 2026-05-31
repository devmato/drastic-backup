from flask import make_response
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required, unset_jwt_cookies
from flask_smorest import Blueprint, abort

from drastic_server.extensions import db
from drastic_server.models.user import User
from drastic_server.schemas.common import MessageSchema
from drastic_server.schemas.user import (
    NeedsInitResponseSchema,
    UserChangePasswordInputSchema,
    UserInitInputSchema,
    UserResponseSchema,
)
from drastic_server.services.auth import SessionAuthService

blp = Blueprint("user", __name__, url_prefix="/api/user", description="User management")


@blp.route("/")
class UserDetail(MethodView):
    @jwt_required()
    @blp.response(200, UserResponseSchema)
    def get(self):
        user = User.query.get(int(get_jwt_identity()))
        if user is None:
            abort(404, message="User not found")
        return user


@blp.route("/init")
class UserInit(MethodView):
    @blp.arguments(UserInitInputSchema)
    @blp.response(201, MessageSchema)
    def post(self, data):
        if User.query.count() > 0:
            abort(400, message="Initial user already exists")

        username = data["username"].strip().lower()
        password = data["password"]

        user = User()
        user.name = username
        user.set_initial_password(password)
        db.session.add(user)
        db.session.commit()

        return {"msg": "User created successfully"}


@blp.route("/password")
class UserPassword(MethodView):
    @jwt_required()
    @blp.arguments(UserChangePasswordInputSchema)
    @blp.response(200, MessageSchema)
    def put(self, data):
        user = User.query.get(int(get_jwt_identity()))
        if user is None:
            abort(404, message="User not found")

        try:
            user.change_password(data["current_password"], data["new_password"])
        except ValueError:
            abort(401, message="Wrong password")

        for session in list(user.sessions):
            SessionAuthService.revoke_session(session)
        db.session.commit()

        response = make_response({"msg": "Password changed. Sign in again."})
        unset_jwt_cookies(response)
        return response


@blp.route("/needs_init")
class UserNeedsInit(MethodView):
    @blp.response(200, NeedsInitResponseSchema)
    def get(self):
        return {"needs_init": User.query.count() == 0}
