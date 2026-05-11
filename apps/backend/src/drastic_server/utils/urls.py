from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

from flask import current_app, has_request_context, request

from drastic_server.env_loader import parse_int_value

_RESTIC_MARKER = "/restic/"


def configured_public_url(config: Mapping[str, Any] | None = None) -> str:
    source = current_app.config if config is None else config
    explicit = explicit_public_url(source)
    if explicit:
        return explicit

    port = parse_int_value(source.get("HOST_BACKEND_PORT"), 5050)
    return f"http://127.0.0.1:{port}"


def public_server_url() -> str:
    explicit = explicit_public_url(current_app.config)
    if explicit:
        return explicit
    if has_request_context():
        return request.host_url.rstrip("/")
    return configured_public_url()


def explicit_public_url(config: Mapping[str, Any] | None = None) -> str | None:
    source = current_app.config if config is None else config
    return _normalize_public_url(source.get("PUBLIC_URL"))


def normalize_restic_repository_path(value: str) -> str:
    segments = [segment for segment in str(value).strip().split("/") if segment not in {"", "."}]
    if not segments:
        raise ValueError("repository path cannot be empty")
    if any(segment == ".." for segment in segments):
        raise ValueError("repository path cannot contain '..'")
    return "/".join(segments)


def build_managed_restic_location(config: Mapping[str, Any], repository_path: str) -> str:
    return build_managed_restic_location_for_server(configured_public_url(config), repository_path)


def build_managed_restic_location_for_server(server_url: str, repository_path: str) -> str:
    normalized_path = normalize_restic_repository_path(repository_path)
    normalized_server_url = str(server_url or "").strip().rstrip("/")
    if not normalized_server_url:
        raise ValueError("server_url cannot be empty")
    return f"rest:{normalized_server_url}/restic/{normalized_path}"


def extract_managed_restic_repository_path(location: str) -> str | None:
    raw_location = str(location or "").strip()
    if not raw_location.startswith("rest:"):
        return None

    parsed = urlparse(raw_location.removeprefix("rest:"))
    marker_index = parsed.path.find(_RESTIC_MARKER)
    if marker_index < 0:
        return None

    repository_path = parsed.path[marker_index + len(_RESTIC_MARKER) :]
    if not repository_path:
        return None

    return normalize_restic_repository_path(repository_path)


def rewrite_managed_restic_location(location: str, server_url: str | None) -> str:
    raw_location = str(location or "").strip()
    effective_server_url = str(server_url or "").strip().rstrip("/")
    if not raw_location.startswith("rest:") or not effective_server_url:
        return raw_location

    parsed = urlparse(raw_location.removeprefix("rest:"))
    server = urlparse(effective_server_url)
    marker_index = parsed.path.find(_RESTIC_MARKER)
    if marker_index < 0:
        return raw_location

    rewritten = parsed._replace(scheme=server.scheme, netloc=server.netloc)
    return f"rest:{urlunparse(rewritten)}"


def _normalize_public_url(value: Any) -> str | None:
    normalized = str(value or "").strip().rstrip("/")
    return normalized or None
