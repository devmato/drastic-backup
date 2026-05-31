from drastic_server.services.agent.artifacts import (
    agent_artifact_path,
    agent_git_repository,
    build_agent_install_targets,
    normalize_agent_platform,
)


def test_agent_artifact_path_uses_requested_version(tmp_path):
    config = {
        "AGENT_RELEASE_BASE_URL": "https://github.com",
        "AGENT_RELEASE_REPOSITORY": "devmato/drastic-backup",
        "AGENT_ARTIFACT_NAME_TEMPLATE": "drastic-agent-{os}-{arch}-{tag}.{ext}",
        "ASSET_CACHE_PATH": str(tmp_path),
    }
    cached_artifact = (
        tmp_path
        / "devmato"
        / "drastic-backup"
        / "v0.2.0"
        / "drastic-agent-linux-amd64-v0.2.0.tar.gz"
    )
    cached_artifact.parent.mkdir(parents=True)
    cached_artifact.write_bytes(b"artifact")

    assert agent_artifact_path(config, "linux", "amd64", "0.2.0") == cached_artifact


def test_agent_artifact_path_supports_linux_arm64_alias(tmp_path):
    config = {
        "AGENT_RELEASE_BASE_URL": "https://github.com",
        "AGENT_RELEASE_REPOSITORY": "devmato/drastic-backup",
        "AGENT_ARTIFACT_NAME_TEMPLATE": "drastic-agent-{os}-{arch}-{tag}.{ext}",
        "ASSET_CACHE_PATH": str(tmp_path),
    }
    cached_artifact = (
        tmp_path
        / "devmato"
        / "drastic-backup"
        / "v0.2.0"
        / "drastic-agent-linux-arm64-v0.2.0.tar.gz"
    )
    cached_artifact.parent.mkdir(parents=True)
    cached_artifact.write_bytes(b"artifact")

    assert normalize_agent_platform("linux", "aarch64") == ("linux", "arm64")
    assert agent_artifact_path(config, "linux", "aarch64", "0.2.0") == cached_artifact


def test_agent_install_targets_include_linux_arm64_native():
    targets = build_agent_install_targets(
        {"AGENT_IMAGE": "ghcr.io/devmato/drastic-backup-agent:latest"}
    )

    linux_arm64 = next(target for target in targets if target["id"] == "linux-arm64-native")

    assert linux_arm64["deployment"] == "native"
    assert linux_arm64["os"] == "linux"
    assert linux_arm64["arch"] == "arm64"


def test_agent_git_repository_uses_release_repository_config():
    config = {
        "AGENT_RELEASE_BASE_URL": "https://github.com/",
        "AGENT_RELEASE_REPOSITORY": "devmato/drastic-backup",
    }

    assert agent_git_repository(config) == "https://github.com/devmato/drastic-backup.git"
