from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from flask import current_app

from drastic_common.restic import ResticApi, ResticRepository
from drastic_common.restic.exceptions import ResticError
from drastic_server.utils.urls import normalize_restic_repository_path


def restic_binary_path() -> str:
    return str(current_app.config.get("RESTIC_BINARY_PATH") or "restic").strip() or "restic"


def init_native_repository(repository_path: str, password: str) -> dict:
    normalized_path = normalize_restic_repository_path(repository_path)
    internal_base_url = str(current_app.config.get("REST_SERVER_URL") or "http://rest-server:8000").rstrip("/")
    repository = ResticRepository(
        location=f"rest:{internal_base_url}/{normalized_path}",
        password=password,
    )
    restic = ResticApi(binary_path=restic_binary_path(), repository=repository)
    restic.init()
    return restic.cat_config()


def ensure_native_repository_initialized(repository_path: str, password: str) -> dict:
    try:
        return init_native_repository(repository_path=repository_path, password=password)
    except ResticError as exc:
        if "config file already exists" not in str(exc):
            raise

    normalized_path = normalize_restic_repository_path(repository_path)
    internal_base_url = str(current_app.config.get("REST_SERVER_URL") or "http://rest-server:8000").rstrip("/")
    repository = ResticRepository(
        location=f"rest:{internal_base_url}/{normalized_path}",
        password=password,
    )
    restic = ResticApi(binary_path=restic_binary_path(), repository=repository)
    return restic.cat_config()


def generate_native_repository_path() -> str:
    return f"native/{secrets.token_hex(16)}"


def generate_native_repository_password() -> str:
    return secrets.token_urlsafe(32)


def native_repository_storage_path(repository_path: str) -> Path:
    normalized_path = normalize_restic_repository_path(repository_path)
    data_root = Path(
        str(current_app.config.get("REST_SERVER_DATA_DIRECTORY") or "/data").strip() or "/data"
    )
    return data_root.joinpath(*normalized_path.split("/"))


def native_repository_has_locks(repository_path: str) -> bool:
    locks_dir = native_repository_storage_path(repository_path) / "locks"
    if not locks_dir.is_dir():
        return False

    return any(entry.is_file() for entry in locks_dir.iterdir())


def delete_native_repository(repository_path: str) -> None:
    storage_path = native_repository_storage_path(repository_path)
    if not storage_path.exists():
        return

    if native_repository_has_locks(repository_path):
        raise RuntimeError("Native repository is currently locked or in use")

    shutil.rmtree(storage_path)
