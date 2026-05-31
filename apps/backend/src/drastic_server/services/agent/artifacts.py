from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

import requests


class AgentArtifactError(RuntimeError):
    pass


class AgentArtifactNotFoundError(AgentArtifactError):
    pass


AGENT_ARTIFACT_TARGETS: tuple[dict[str, str], ...] = (
    {
        "os": "linux",
        "arch": "amd64",
        "label": "Linux x64",
        "ext": "tar.gz",
        "description": "Run the Linux x64 agent directly on a host.",
    },
    {
        "os": "linux",
        "arch": "arm64",
        "label": "Linux arm64",
        "ext": "tar.gz",
        "description": "Run the Linux arm64 agent directly on a host.",
    },
    {
        "os": "windows",
        "arch": "amd64",
        "label": "Windows x64",
        "ext": "zip",
        "description": "Run the Windows x64 agent directly on a host.",
    },
    {
        "os": "darwin",
        "arch": "amd64",
        "label": "macOS Intel",
        "ext": "tar.gz",
        "description": "Run the macOS Intel agent directly on a host.",
    },
)

_TARGETS_BY_PLATFORM = {(target["os"], target["arch"]): target for target in AGENT_ARTIFACT_TARGETS}

_OS_ALIASES = {
    "macos": "darwin",
    "osx": "darwin",
}

_ARCH_ALIASES = {
    "x64": "amd64",
    "x86_64": "amd64",
    "aarch64": "arm64",
}


def normalize_agent_platform(os_name: str, arch: str) -> tuple[str, str]:
    normalized_os = _OS_ALIASES.get(str(os_name or "").strip().lower(), str(os_name or "").strip().lower())
    normalized_arch = _ARCH_ALIASES.get(str(arch or "").strip().lower(), str(arch or "").strip().lower())
    if (normalized_os, normalized_arch) not in _TARGETS_BY_PLATFORM:
        raise AgentArtifactNotFoundError(f"Unsupported agent platform: {os_name}/{arch}")
    return normalized_os, normalized_arch


def agent_artifact_path(
    config: Mapping[str, Any], os_name: str, arch: str, requested_version: str | None = None
) -> Path:
    os_name, arch = normalize_agent_platform(os_name, arch)
    target = _TARGETS_BY_PLATFORM[(os_name, arch)]
    tag = _agent_artifact_tag(config, requested_version)
    asset_name = _agent_artifact_name(config, target, tag)
    cached_path = _asset_cache_path(config, tag, asset_name)
    if cached_path.is_file():
        return cached_path

    _download_asset(_asset_download_url(config, tag, asset_name), cached_path)
    return cached_path


def build_agent_install_targets(config: Mapping[str, Any]) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []

    for target in AGENT_ARTIFACT_TARGETS:
        os_name = target["os"]
        arch = target["arch"]
        targets.append(
            {
                "id": f"{os_name}-{arch}-native",
                "label": target["label"],
                "platform": os_name,
                "os": os_name,
                "arch": arch,
                "deployment": "native",
                "description": target["description"],
                "artifact_type": target["ext"],
            }
        )

    agent_image = _agent_image(config)
    for arch, label in (("amd64", "Docker Linux x64"), ("arm64", "Docker Linux arm64")):
        targets.append(
            {
                "id": f"linux-{arch}-docker",
                "label": label,
                "platform": "linux",
                "os": "linux",
                "arch": arch,
                "deployment": "docker",
                "description": "Run the Linux agent in Docker with persistent data and readonly host access.",
                "image": agent_image,
            }
        )

    return targets


def agent_git_repository(config: Mapping[str, Any]) -> str:
    base_url = str(config.get("AGENT_RELEASE_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        return ""
    try:
        repository = "/".join(_release_repository_segments(config))
    except AgentArtifactError:
        return ""
    return f"{base_url}/{repository}.git"


def _agent_image(config: Mapping[str, Any]) -> str:
    return str(config.get("AGENT_IMAGE") or "").strip()


def _agent_artifact_name(config: Mapping[str, Any], target: Mapping[str, str], tag: str) -> str:
    template = str(
        config.get("AGENT_ARTIFACT_NAME_TEMPLATE") or "drastic-agent-{os}-{arch}-{tag}.{ext}"
    )
    asset_name = template.format(os=target["os"], arch=target["arch"], tag=tag, ext=target["ext"])
    if "/" in asset_name or "\\" in asset_name or asset_name in {"", ".", ".."}:
        raise AgentArtifactError("Invalid agent artifact name template")
    return asset_name


def _download_asset(url: str, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f".{target_path.name}.tmp")
    try:
        with requests.get(url, headers={"User-Agent": "drastic-backup"}, timeout=120, stream=True) as response:
            if response.status_code == 404:
                raise AgentArtifactNotFoundError(f"Release asset not found: {target_path.name}")
            response.raise_for_status()
            with tmp_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
        tmp_path.replace(target_path)
    except requests.RequestException as exc:
        raise AgentArtifactError(f"Could not download release asset: {exc}") from exc
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _asset_cache_root(config: Mapping[str, Any]) -> Path:
    return Path(str(config.get("ASSET_CACHE_PATH") or "/opt/drastic-server/asset-cache")).resolve()


def _asset_cache_path(config: Mapping[str, Any], tag: str, asset_name: str) -> Path:
    path = _asset_cache_root(config)
    for segment in _release_repository_segments(config):
        path /= segment
    return path / _cache_segment(tag) / asset_name


def _asset_download_url(config: Mapping[str, Any], tag: str, asset_name: str) -> str:
    base_url = str(config.get("AGENT_RELEASE_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        raise AgentArtifactError("Agent release base URL is required")

    repository = "/".join(quote(segment, safe="") for segment in _release_repository_segments(config))
    return f"{base_url}/{repository}/releases/download/{quote(tag, safe='')}/{quote(asset_name, safe='')}"


def _release_repository_segments(config: Mapping[str, Any]) -> list[str]:
    repository = str(config.get("AGENT_RELEASE_REPOSITORY") or "").strip().strip("/")
    segments = repository.split("/") if repository else []
    if not segments or any(segment in {"", ".", ".."} or "\\" in segment for segment in segments):
        raise AgentArtifactError("Agent release repository is required")
    return segments


def _agent_artifact_tag(config: Mapping[str, Any], requested_version: str | None = None) -> str:
    requested = str(requested_version or "").strip()
    if requested and requested != "latest":
        return requested if requested.startswith("v") else f"v{requested}"

    configured = str(config.get("AGENT_ARTIFACT_TAG") or "").strip()
    if configured:
        if configured == "latest":
            raise AgentArtifactError("AGENT_ARTIFACT_TAG must be a concrete release tag, not latest")
        return configured

    return f"v{_backend_version()}"


def _backend_version() -> str:
    try:
        return version("drastic-backup-server")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
        if pyproject_path.is_file():
            pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            project_version = str(pyproject.get("project", {}).get("version") or "").strip()
            if project_version:
                return project_version
    raise AgentArtifactError("Could not determine backend version for agent artifact tag")


def _cache_segment(value: str) -> str:
    segment = quote(str(value), safe="")
    if segment in {"", ".", ".."}:
        raise AgentArtifactError("Invalid agent artifact cache segment")
    return segment
