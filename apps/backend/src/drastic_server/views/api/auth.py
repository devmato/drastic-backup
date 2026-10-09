from flask import current_app, make_response, request
from flask.views import MethodView
from flask_jwt_extended import (
    get_jwt,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
from flask_smorest import Blueprint

from drastic_server.models.user import User
from drastic_server.schemas.auth import (
    AuthStatusResponseSchema,
    LoginInputSchema,
    LoginResponseSchema,
)
from drastic_server.services.auth import SessionAuthService
from drastic_server.views.api.errors import register_service_errors

blp = Blueprint("auth", __name__, url_prefix="/api/auth", description="Authentication")
register_service_errors(blp)


@blp.route("/login")
class AuthLogin(MethodView):
    @blp.arguments(LoginInputSchema)
    @blp.response(200, LoginResponseSchema)
    def post(self, data):
        recovery_key, access_token, refresh_token = SessionAuthService.login(
            data["username"], data["password"], user_agent=request.user_agent.string or "",
        )
        response = make_response({"ok": True, "recovery_key": recovery_key})
        set_access_cookies(response, access_token)
        set_refresh_cookies(response, refresh_token)
        return response


@blp.route("/refresh")
class AuthRefresh(MethodView):
    @jwt_required(refresh=True, locations=["cookies"])
    @blp.response(200, LoginResponseSchema)
    def post(self):
        session_public_id = str(get_jwt().get("sid") or "").strip()
        refresh_cookie_name = current_app.config.get(
            "JWT_REFRESH_COOKIE_NAME", "refresh_token_cookie"
        )
        refresh_token = request.cookies.get(str(refresh_cookie_name))
        access_token, refresh_token = SessionAuthService.refresh(
            session_public_id, refresh_token, user_agent=request.user_agent.string or "",
        )
        response = make_response({"ok": True})
        set_access_cookies(response, access_token)
        set_refresh_cookies(response, refresh_token)
        return response


@blp.route("/logout")
class AuthLogout(MethodView):
    @blp.response(200, LoginResponseSchema)
    def post(self):
        session_public_id = _extract_session_public_id_from_request()
        SessionAuthService.logout(session_public_id)

        response = make_response({"ok": True})
        unset_jwt_cookies(response)
        return response


@blp.route("/status")
class AuthStatus(MethodView):
    @jwt_required(optional=True)
    @blp.response(200, AuthStatusResponseSchema)
    def get(self):
        environment = str(current_app.config.get("DRASTIC_ENV") or "dev")
        user_id = get_jwt_identity()
        if user_id:
            user = User.query.get(int(user_id))
            if user:
                return {"authenticated": True, "user": user, "environment": environment}
        return {"authenticated": False, "user": None, "environment": environment}


def _extract_session_public_id_from_request() -> str | None:
    cookie_names = (
        str(current_app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie")),
        str(current_app.config.get("JWT_REFRESH_COOKIE_NAME", "refresh_token_cookie")),
    )

    for cookie_name in cookie_names:
        session_public_id = SessionAuthService.decode_session_public_id(
            request.cookies.get(cookie_name), allow_expired=True
        )
        if session_public_id:
            return session_public_id

    return None
