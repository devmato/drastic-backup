from __future__ import annotations

from urllib.parse import unquote

import bcrypt
import requests
from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from drastic_server.env_loader import parse_int_value
from drastic_server.models.agent import Agent
from drastic_server.models.repository import Repository

blp = Blueprint("restic_proxy", __name__)

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class _SizedRequestStream:
    def __init__(self, stream, content_length: int | None):
        self._stream = stream
        self._content_length = content_length
        self._remaining = content_length

    def __len__(self):
        if self._content_length is None:
            raise TypeError("Request body length is unknown")

        return self._content_length

    def read(self, size: int | None = None):
        if self._remaining == 0:
            return b""

        if self._remaining is None:
            return self._stream.read(size)

        if size is None or size < 0:
            size = self._remaining
        else:
            size = min(size, self._remaining)

        data = self._stream.read(size)
        if data:
            self._remaining -= len(data)

        return data


@blp.route("/restic/", defaults={"proxy_path": ""}, methods=["GET", "HEAD", "POST", "DELETE"])
@blp.route("/restic/<path:proxy_path>", methods=["GET", "HEAD", "POST", "DELETE"])
def proxy_restic(proxy_path: str):
    agent = _authenticate_agent()
    if agent is None:
        return _auth_failed("Invalid or missing agent credentials")

    try:
        normalized_proxy_path, has_trailing_slash = _normalize_proxy_path(proxy_path)
    except ValueError:
        return jsonify({"msg": "Invalid restic path"}), 400

    repository = _authorize_repository(agent, normalized_proxy_path)
    if repository is None:
        return jsonify({"msg": "Repository access denied"}), 403

    upstream_path = f"/{normalized_proxy_path}"
    if has_trailing_slash:
        upstream_path = f"{upstream_path}/"
    upstream_url = f"{str(current_app.config.get('REST_SERVER_URL') or 'http://rest-server:8000').rstrip('/')}{upstream_path}"

    try:
        proxy_timeout = parse_int_value(current_app.config.get("REST_PROXY_TIMEOUT_SECONDS"), 300)
        upstream = requests.request(
            method=request.method,
            url=upstream_url,
            params=list(request.args.items(multi=True)),
            headers=_forward_headers(agent.id, repository.id),
            data=_forward_request_body(),
            stream=True,
            timeout=(5, proxy_timeout),
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        current_app.logger.warning("Restic proxy request timed out: %s", exc)
        return jsonify({"msg": "Rest-server request timed out"}), 504
    except requests.RequestException as exc:
        current_app.logger.warning("Restic proxy request failed: %s", exc)
        return jsonify({"msg": "Could not reach rest-server"}), 502

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }

    if request.method == "HEAD":
        upstream.close()
        return Response(status=upstream.status_code, headers=response_headers)

    response = Response(
        stream_with_context(upstream.iter_content(chunk_size=64 * 1024)),
        status=upstream.status_code,
        headers=response_headers,
    )
    response.call_on_close(upstream.close)
    return response


def _authenticate_agent() -> Agent | None:
    auth = request.authorization
    if auth is None or str(auth.type or "").lower() != "basic":
        return None

    try:
        agent_id = int(str(auth.username or "").strip())
    except ValueError:
        return None

    password = auth.password
    if password is None:
        return None

    agent = Agent.query.filter(Agent.id == agent_id).first()
    if agent is None:
        return None

    if not bcrypt.checkpw(password.encode("utf-8"), agent.secret.encode("utf-8")):
        return None

    return agent


def _authorize_repository(agent: Agent, proxy_path: str) -> Repository | None:
    normalized_request_path = str(proxy_path or "").strip("/")
    if not normalized_request_path:
        return None

    repositories = agent.repositories

    best_match = None
    best_match_length = -1
    for repository in repositories:
        if repository.kind != Repository.KIND_NATIVE:
            continue

        repository_path = repository.repository_path
        if repository_path is None:
            continue
        if normalized_request_path != repository_path and not normalized_request_path.startswith(
            f"{repository_path}/"
        ):
            continue
        if len(repository_path) > best_match_length:
            best_match = repository
            best_match_length = len(repository_path)

    return best_match


def _normalize_proxy_path(proxy_path: str) -> tuple[str, bool]:
    raw_path = str(proxy_path or "")
    has_trailing_slash = raw_path.endswith("/")
    decoded_path = raw_path
    for _ in range(10):
        next_decoded_path = unquote(decoded_path)
        if next_decoded_path == decoded_path:
            break
        decoded_path = next_decoded_path
    else:
        raise ValueError("restic path is too deeply encoded")

    segments = []
    for segment in decoded_path.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            raise ValueError("restic path cannot contain '..'")
        segments.append(segment)

    if not segments:
        raise ValueError("restic path cannot be empty")

    return "/".join(segments), has_trailing_slash


def _forward_headers(agent_id: int, repository_id: int) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lower_key = key.lower()
        if lower_key in _HOP_BY_HOP_HEADERS or lower_key in {
            "host",
            "authorization",
            "cookie",
            "expect",
        }:
            continue
        headers[key] = value

    headers["X-Drastic-Agent-Id"] = str(agent_id)
    headers["X-Drastic-Repository-Id"] = str(repository_id)
    return headers


def _forward_request_body():
    if request.method not in {"POST", "PUT", "PATCH"}:
        return None

    if request.content_length == 0:
        return b""

    if request.content_length is None:
        return request.get_data(cache=False) or b""

    return _SizedRequestStream(request.stream, request.content_length)


def _auth_failed(message: str):
    response = jsonify({"msg": message})
    response.status_code = 401
    response.headers["WWW-Authenticate"] = 'Basic realm="drastic-restic"'
    return response
