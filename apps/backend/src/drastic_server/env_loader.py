from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, MutableMapping


def application_root() -> Path:
    return Path(__file__).resolve().parents[2]


def repository_root() -> Path:
    for candidate in (application_root(), *application_root().parents):
        if (candidate / ".env.default").is_file():
            return candidate
    return application_root()


def normalize_environment_name(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or "dev"


def environment_file_paths(*, root: Path | None = None, environment: str) -> list[Path]:
    repo_root = root or repository_root()
    env_name = normalize_environment_name(environment)
    return [
        repo_root / ".env.default",
        repo_root / f".env.{env_name}",
        repo_root / f".env.{env_name}.override",
    ]


def apply_runtime_environment_defaults(
    *,
    root: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> str:
    target_env = os.environ if environ is None else environ
    repo_root = root or repository_root()
    merged_values: dict[str, str] = {}

    default_path = repo_root / ".env.default"
    merged_values.update(_read_env_file(default_path))

    active_environment = normalize_environment_name(
        target_env.get("DRASTIC_ENV") or merged_values.get("DRASTIC_ENV")
    )

    for env_path in environment_file_paths(root=repo_root, environment=active_environment)[1:]:
        merged_values.update(_read_env_file(env_path))

    merged_values.setdefault("DRASTIC_ENV", active_environment)

    for key, value in merged_values.items():
        if value is None:
            continue
        target_env.setdefault(key, value)

    target_env.setdefault("DRASTIC_ENV", active_environment)
    return active_environment


def apply_drastic_environment_config(
    app_config: MutableMapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    source_env = os.environ if environ is None else environ
    for key in sorted(source_env):
        if not key.startswith("DRASTIC_") or key == "DRASTIC_ENV":
            continue
        config_key = key.removeprefix("DRASTIC_")
        app_config[config_key] = _parse_env_value(source_env[key])

    app_config["DRASTIC_ENV"] = normalize_environment_name(source_env.get("DRASTIC_ENV"))


def parse_int_value(value: Any, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def default_dev_cors_origins(frontend_port: Any = 9050) -> list[str]:
    port = parse_int_value(frontend_port, 9050)
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        parsed[key] = value

    return parsed


def _parse_env_value(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value
