"""Repository endpoint resolution and recovery-key provisioning."""

import os
from urllib.parse import urlparse, urlunparse

from drastic_agent.agent.exceptions import AgentExeption
from drastic_common.agent.enums import AgentRepositoryKind
from drastic_common.restic.exceptions import ResticError


def _rewrite_location(location, server_url):
    location = str(location or "").strip()
    server_url = str(server_url or "").strip().rstrip("/")
    if not location.startswith("rest:") or not server_url:
        return location
    parsed = urlparse(location.removeprefix("rest:"))
    if "/restic/" not in parsed.path:
        return location
    server = urlparse(server_url)
    return f"rest:{urlunparse(parsed._replace(scheme=server.scheme, netloc=server.netloc))}"


def location_environment(repository, *, identifier, server_url, agent_secret, rewrite_managed=False):
    env = dict(repository.get("environment") or {})
    if identifier:
        env.setdefault("RESTIC_HOST", os.environ.get("RESTIC_HOST") or f"drastic-{identifier}")
    location = repository["location"]
    if repository.get("kind") == AgentRepositoryKind.native.value:
        if str(location).startswith("rest:"):
            location = _rewrite_location(location, server_url)
        else:
            server = str(server_url or "").strip().rstrip("/")
            path = str(location or "").strip().strip("/")
            location = f"rest:{server}/restic/{path}" if server and path else str(location or "")
    elif rewrite_managed:
        location = _rewrite_location(location, server_url)
    if location.startswith("rest:") and "/restic/" in location and identifier and agent_secret:
        env["RESTIC_REST_USERNAME"] = str(identifier)
        env["RESTIC_REST_PASSWORD"] = agent_secret
    return location, env


def provision_key(restic, recovery_repository, agent_password, *, repository_id, initialize):
    """Verify recovery access before adding a dedicated agent key."""
    restic.set_repository(recovery_repository)
    if initialize:
        try:
            restic.init()
        except ResticError as exc:
            message = str(exc).lower()
            if "already initialized" not in message and "config file already exists" not in message:
                raise AgentExeption(f"Repository {repository_id} initialization failed: {exc}") from exc
    else:
        try:
            restic.cat_config()
        except ResticError as exc:
            raise AgentExeption(f"Repository {repository_id} recovery password failed: {exc}") from exc
    try:
        restic.key_add(agent_password)
    except ResticError as exc:
        raise AgentExeption(f"Repository {repository_id} agent key provisioning failed: {exc}") from exc
