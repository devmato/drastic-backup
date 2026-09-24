import subprocess

from drastic_server.services.agent.installer import (
    build_agent_install_targets,
    render_linux_agent_install_script,
)


def test_pipe_installer_quotes_configuration_and_preserves_arguments():
    script = render_linux_agent_install_script(
        "https://backup.example.net/a'b", "https://example.net/team/backup.git"
    )
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    # Exit before any bootstrap side effects, after reading the generated defaults.
    prefix = script.split("ROOT=/opt/drastic-agent", 1)[0]
    result = subprocess.run(
        ["bash", "-c", prefix + 'printf "%s\\n" "$DRASTIC_SERVER" "$DRASTIC_AGENT_GIT_REPOSITORY"'],
        text=True, capture_output=True, check=True,
    )
    assert result.stdout.splitlines() == [
        "https://backup.example.net/a'b", "https://example.net/team/backup.git"
    ]


def test_docker_targets_keep_both_architectures_and_configured_image():
    targets = build_agent_install_targets({"AGENT_IMAGE": "registry.example/agent:v1.2.3"})
    assert {target["arch"] for target in targets} == {"amd64", "arm64"}
    assert all(target["image"] == "registry.example/agent:v1.2.3" for target in targets)
