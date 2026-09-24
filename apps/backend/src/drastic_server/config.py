from __future__ import annotations

import json
import os
from typing import Any, Mapping, MutableMapping


class DefaultConfig:
    DRASTIC_ENV = "prod"

    AGENT_IMAGE = "ghcr.io/devmato/drastic-backup-agent:latest"
    AGENT_GIT_REPOSITORY = "https://github.com/devmato/drastic-backup.git"

    APP_NAME = "drastic-backup"
    API_TITLE = "dRastic Backup API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.0.2"
    OPENAPI_URL_PREFIX = "/api"
    OPENAPI_SWAGGER_UI_PATH = "/docs"
    OPENAPI_SWAGGER_UI_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"
    CORS_ORIGINS = None
    SOCKETIO_CORS_ORIGINS = None

    APP_MASTER_SECRET = ""
    SECRET_KEY = ""
    JWT_SECRET_KEY = ""
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_REFRESH_TOKEN_EXPIRES = 60 * 60 * 24 * 30
    JWT_SESSION_COOKIE = False
    JWT_COOKIE_SECURE = False
    JWT_COOKIE_SAMESITE = "Lax"
    JWT_COOKIE_CSRF_PROTECT = True
    JWT_REFRESH_COOKIE_PATH = "/api/auth"
    JWT_ACCESS_COOKIE_PATH = "/"
    JWT_COOKIE_DOMAIN = ""
    TESTING = False

    BIND_BACKEND_PORT = 5050
    HOST_BACKEND_PORT = 5050
    BIND_DEV_FRONTEND_PORT = 9050
    HOST_DEV_FRONTEND_PORT = 9050

    PUBLIC_URL = ""
    REST_SERVER_URL = "http://rest-server:8000"
    REST_SERVER_DATA_DIRECTORY = "/data"
    DOCS_SITE_PATH = "/app/docs-site"
    RESTIC_BINARY_PATH = "restic"

    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False

    GUNICORN_WORKERS = 1
    GUNICORN_THREADS = 16
    GUNICORN_TIMEOUT = 300
    GUNICORN_GRACEFUL_TIMEOUT = 60
    GUNICORN_KEEPALIVE = 5
    GUNICORN_LOG_LEVEL = "info"

    REST_PROXY_TIMEOUT_SECONDS = 300
    COMMAND_TIMEOUT_SECONDS = 60

    BOOTSTRAP_ADMIN_USERNAME = ""
    BOOTSTRAP_ADMIN_PASSWORD = ""
    SERVER_NAME = None


def normalize_environment_name(value: str | None) -> str:
    return str(value or "").strip().lower() or DefaultConfig.DRASTIC_ENV


def apply_environment_config(
    app_config: MutableMapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    source = os.environ if environ is None else environ
    for key in sorted(source):
        if not key.startswith("DRASTIC_") or key == "DRASTIC_ENV":
            continue
        app_config[key.removeprefix("DRASTIC_")] = _parse_env_value(source[key])

    app_config["DRASTIC_ENV"] = normalize_environment_name(source.get("DRASTIC_ENV"))


def validate_config(config: Mapping[str, Any]) -> None:
    missing = [
        name
        for name in ("APP_MASTER_SECRET", "SQLALCHEMY_DATABASE_URI")
        if not str(config.get(name) or "").strip()
    ]
    if missing:
        variables = ", ".join(f"DRASTIC_{name}" for name in missing)
        raise RuntimeError(f"Missing required configuration: {variables}")


def env_int(name: str, default: int) -> int:
    return parse_int_value(os.environ.get(name), default)


def parse_int_value(value: Any, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def default_dev_cors_origins(
    frontend_port: Any = DefaultConfig.BIND_DEV_FRONTEND_PORT,
) -> list[str]:
    port = parse_int_value(frontend_port, DefaultConfig.BIND_DEV_FRONTEND_PORT)
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]


def _parse_env_value(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value
