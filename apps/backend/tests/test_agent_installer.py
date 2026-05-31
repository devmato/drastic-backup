from drastic_server.services.agent.installer import render_linux_agentctl_script


def test_linux_agentctl_supports_arm64_architecture():
    script = render_linux_agentctl_script("https://backup.example.net")

    assert "aarch64|arm64) ARCH=arm64 ;;" in script
