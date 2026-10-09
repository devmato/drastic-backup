from __future__ import annotations

import os


class DefaultConfig:
    ENV = "prod"
    AGENT_DATA_DIR = "/app/data"
    AGENT_MAX_WORKERS = 2
    AGENT_MAX_PENDING = 8
    AGENT_HOT_RELOAD_DEBOUNCE_MS = 2500
    AGENT_REWRITE_MANAGED_REPO_URLS = False
    COMMAND_TIMEOUT_SECONDS = 60
    TASK_TIMEOUT_SECONDS = 600
    RESTIC_TIMEOUT_SECONDS = 86400
    PROXMOX_API_URL = "https://127.0.0.1:8006/api2/json"
    PROXMOX_VERIFY_TLS = False


def env_value(name: str, default: str) -> str:
    return str(os.environ.get(name) or default).strip()


def env_int(name: str, default: int) -> int:
    try:
        parsed = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def env_flag(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}
