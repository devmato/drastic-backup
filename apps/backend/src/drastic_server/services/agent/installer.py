"""Expose the running build as the reproducible target for managed agents."""

import re
from pathlib import Path
from shlex import quote

from drastic_common.version import get_revision, get_version
from drastic_server.config import DefaultConfig
from drastic_server.services.exceptions import ResourceConflict


def installation_target():
    revision, version = get_revision(), get_version()
    if revision is None or not re.fullmatch(rf"[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}-{revision[:8]}", version):
        raise ResourceConflict("This backend has no reproducible agent installation target; use a published build or --source for local development")
    return {"revision": revision, "version": version}


def render_linux_agent_install_script(server_url: str, repository: str) -> str:
    # The production image packages the same script used directly from Git.
    script = Path(__file__).with_name("install.sh")
    if not script.is_file():
        script = Path(__file__).resolve().parents[6] / "scripts/install-drastic-agent.sh"
    return (
        "#!/usr/bin/env bash\n"
        f"export DRASTIC_SERVER={quote(server_url.rstrip('/'))}\n"
        f"export DRASTIC_AGENT_GIT_REPOSITORY={quote(repository)}\n"
        + script.read_text()
    )


def read_linux_agent_manager_script() -> str:
    """Provide the current controller even when bootstrap selects an older agent source."""
    script = Path(__file__).with_name("install.py")
    if not script.is_file():
        script = Path(__file__).resolve().parents[6] / "scripts/drastic-agent-installer.py"
    return script.read_text()


def build_agent_install_targets(config):
    return [
        {
            "id": f"linux-{arch}-docker",
            "os": "linux",
            "arch": arch,
            "deployment": "docker",
            "image": config.get("AGENT_IMAGE") or DefaultConfig.AGENT_IMAGE,
        }
        for arch in ("amd64", "arm64")
    ]
