from pathlib import Path
from shlex import quote

from drastic_server.config import DefaultConfig


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
