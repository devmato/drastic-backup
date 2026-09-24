import atexit
import hashlib
import hmac
import logging
from pathlib import Path

from flask import Flask, Response, jsonify, send_file, send_from_directory

from drastic_server import models as _models  # noqa: F401
from drastic_server.cli import register_cli
from drastic_server.config import (
    DefaultConfig,
    apply_environment_config,
    default_dev_cors_origins,
    parse_int_value,
    validate_config,
)
from drastic_server.extensions import db, jwt, migrate, smorest_api, socketio
from drastic_server.models.user import User
from drastic_server.services.agent import (
    render_linux_agent_install_script,
)
from drastic_server.services.auth import SessionAuthService
from drastic_server.socketio.namespaces import agent as _agent_namespace  # noqa: F401
from drastic_server.socketio.namespaces import web as _web_namespace  # noqa: F401
from drastic_server.views.api import blueprints as api_blueprints
from drastic_server.views.restic_proxy import blp as restic_proxy_blp

_INSECURE_MASTER_SECRETS = frozenset({"change-me-local-secret", ""})


# Called on server shutdown
def cleanup_app():
    print("Stopping drastic-server...")


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(DefaultConfig)

    atexit.register(cleanup_app)

    if config_object:
        app.config.from_object(config_object)

    apply_environment_config(app.config)
    validate_config(app.config)

    _derive_application_secrets(app)
    _derive_settings_encryption_key(app)
    app.config["BIND_BACKEND_PORT"] = parse_int_value(
        app.config.get("BIND_BACKEND_PORT"), DefaultConfig.BIND_BACKEND_PORT
    )
    app.config["HOST_BACKEND_PORT"] = parse_int_value(
        app.config.get("HOST_BACKEND_PORT"), app.config["BIND_BACKEND_PORT"]
    )
    app.config["BIND_DEV_FRONTEND_PORT"] = parse_int_value(
        app.config.get("BIND_DEV_FRONTEND_PORT"), DefaultConfig.BIND_DEV_FRONTEND_PORT
    )
    app.config["HOST_DEV_FRONTEND_PORT"] = parse_int_value(
        app.config.get("HOST_DEV_FRONTEND_PORT"), app.config["BIND_DEV_FRONTEND_PORT"]
    )
    if app.config.get("CORS_ORIGINS") is None:
        app.config["CORS_ORIGINS"] = default_dev_cors_origins(app.config["HOST_DEV_FRONTEND_PORT"])

    _warn_insecure_master_secret(app)

    # Register flask app object to extensions
    jwt.init_app(app)
    db.init_app(app)
    migrate.init_app(app, db)
    smorest_api.init_app(app)
    socketio.init_app(
        app,
        cors_allowed_origins=app.config.get("SOCKETIO_CORS_ORIGINS") or "*",
    )
    app.register_blueprint(restic_proxy_blp)
    # Register API blueprints via flask-smorest
    for blp in api_blueprints:
        smorest_api.register_blueprint(blp)

    # Register CLI commands
    register_cli(app)
    _register_install_route(app)
    _register_docs_routes(app)
    _register_spa_routes(app)

    # JWT user lookup callback
    @jwt.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        identity = jwt_data["sub"]
        return db.session.get(User, int(identity))

    return app


def _register_install_route(app: Flask) -> None:
    @app.get("/install")
    def serve_agent_installer():
        from drastic_server.utils.urls import public_server_url

        return Response(
            render_linux_agent_install_script(public_server_url(), app.config["AGENT_GIT_REPOSITORY"]),
            content_type="text/x-shellscript; charset=utf-8",
        )


def _register_spa_routes(app: Flask) -> None:
    app.config.setdefault("SPA_ROOT", str(Path(app.root_path).parents[1] / "spa"))

    @app.get("/app")
    @app.get("/app/")
    @app.get("/app/<path:filename>")
    def serve_spa(filename: str = "index.html"):
        spa_root = Path(app.config["SPA_ROOT"]).resolve()
        requested_file = (spa_root / filename).resolve()
        if requested_file.is_file() and requested_file.is_relative_to(spa_root):
            return send_from_directory(spa_root, filename, as_attachment=False)
        return send_file(spa_root / "index.html")


def _register_docs_routes(app: Flask) -> None:
    docs_root = Path(str(app.config.get("DOCS_SITE_PATH") or "")).resolve()
    if not (docs_root / "index.html").is_file():
        return

    @app.get("/docs/")
    def serve_docs_root():
        return send_file(docs_root / "index.html")

    @app.get("/docs/<path:filename>")
    def serve_docs(filename: str):
        requested_file = (docs_root / filename).resolve()
        if requested_file.is_dir() and requested_file.is_relative_to(docs_root):
            index_path = requested_file / "index.html"
            if index_path.is_file():
                return send_file(index_path)
        return send_from_directory(docs_root, filename, as_attachment=False)


def _derive_application_secrets(app: Flask) -> None:
    explicit_secret = str(app.config.get("SECRET_KEY") or "").strip()
    explicit_jwt_secret = str(app.config.get("JWT_SECRET_KEY") or "").strip()
    master_secret = str(app.config.get("APP_MASTER_SECRET") or "").strip()

    app.config["APP_MASTER_SECRET"] = master_secret

    if explicit_secret:
        app.config["SECRET_KEY"] = explicit_secret
    else:
        app.config["SECRET_KEY"] = _derive_secret(master_secret, b"drastic:flask-session")

    if explicit_jwt_secret:
        app.config["JWT_SECRET_KEY"] = explicit_jwt_secret
    else:
        app.config["JWT_SECRET_KEY"] = _derive_secret(master_secret, b"drastic:jwt-signing")


def _derive_settings_encryption_key(app: Flask) -> None:
    master_secret = str(app.config.get("APP_MASTER_SECRET") or "").strip()
    app.config["ENCRYPTION_KEY"] = _derive_secret(master_secret, b"drastic:settings-encryption")


def _derive_secret(master_secret: str, purpose: bytes) -> str:
    return hmac.new(master_secret.encode(), purpose, hashlib.sha256).hexdigest()


def _warn_insecure_master_secret(app: Flask) -> None:
    logger = logging.getLogger(__name__)
    master_secret = str(app.config.get("APP_MASTER_SECRET") or "").strip()
    if app.config.get("SECRET_KEY") and app.config.get("JWT_SECRET_KEY"):
        if master_secret in _INSECURE_MASTER_SECRETS or len(master_secret) < 16:
            logger.warning(
                "APP_MASTER_SECRET is insecure. Set DRASTIC_APP_MASTER_SECRET before deploying to production."
            )


@jwt.token_in_blocklist_loader
def token_in_blocklist_callback(_jwt_header, jwt_payload):
    session_public_id = jwt_payload.get("sid")
    if not session_public_id:
        return False
    return SessionAuthService.get_active_session_by_public_id(str(session_public_id)) is None


def _jwt_auth_error_response(message: str, *, code: str):
    return jsonify({"message": message, "code": code}), 401


@jwt.invalid_token_loader
def invalid_token_callback(reason: str):
    return _jwt_auth_error_response("Invalid session", code="invalid_token")


@jwt.expired_token_loader
def expired_token_callback(_jwt_header, _jwt_payload):
    return _jwt_auth_error_response("Session expired", code="token_expired")


@jwt.unauthorized_loader
def unauthorized_callback(reason: str):
    return _jwt_auth_error_response("Authentication required", code="missing_token")


@jwt.revoked_token_loader
def revoked_token_callback(_jwt_header, _jwt_payload):
    return _jwt_auth_error_response("Invalid session", code="token_revoked")


@jwt.needs_fresh_token_loader
def needs_fresh_token_callback(_jwt_header, _jwt_payload):
    return _jwt_auth_error_response("Fresh login required", code="fresh_token_required")


@jwt.user_lookup_error_loader
def user_lookup_error_callback(_jwt_header, _jwt_payload):
    return _jwt_auth_error_response("Invalid session", code="user_lookup_failed")
