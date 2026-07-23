from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from flask import current_app

from drastic_common.restic import ResticApi, ResticRepository
from drastic_common.restic.exceptions import ResticError
from drastic_server.utils.urls import normalize_restic_repository_path


@dataclass(frozen=True)
class NativeRepositoryQuarantine:
    original_path: Path
    quarantined_path: Path

    @property
    def entry_path(self) -> Path:
        return self.quarantined_path.parent


_QUARANTINE_MANIFEST = "manifest.json"
_QUARANTINE_PAYLOAD = "repository"
_QUARANTINE_MANIFEST_VERSION = 1
_MAX_MANIFEST_BYTES = 4096


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
    normalized_path = _normalize_native_repository_path(repository_path)
    path_segments = normalized_path.split("/")
    data_root = _native_repository_data_root()
    storage_path = data_root.joinpath(*path_segments)
    resolved_storage_path = storage_path.resolve()
    if not resolved_storage_path.is_relative_to(data_root) or resolved_storage_path != storage_path:
        raise RuntimeError("Invalid native repository path")
    return storage_path


def native_repository_has_locks(repository_path: str) -> bool:
    locks_dir = native_repository_storage_path(repository_path) / "locks"
    if not locks_dir.is_dir():
        return False

    return any(entry.is_file() for entry in locks_dir.iterdir())


def quarantine_native_repository(repository_path: str) -> NativeRepositoryQuarantine | None:
    normalized_path = _normalize_native_repository_path(repository_path)
    storage_path = native_repository_storage_path(repository_path)
    if not storage_path.exists():
        return None
    if not storage_path.is_dir():
        raise RuntimeError("Invalid native repository path")

    if native_repository_has_locks(repository_path):
        raise RuntimeError("Native repository is currently locked or in use")

    quarantine_root = _native_repository_quarantine_root(create=True)
    entry_path = quarantine_root / uuid4().hex
    entry_path.mkdir(mode=0o700)
    _fsync_directory(quarantine_root)

    quarantine = NativeRepositoryQuarantine(
        original_path=storage_path,
        quarantined_path=entry_path / _QUARANTINE_PAYLOAD,
    )
    moved = False
    try:
        _write_quarantine_manifest(entry_path, normalized_path)
        storage_path.rename(quarantine.quarantined_path)
        moved = True
        _fsync_directory(storage_path.parent)
        _fsync_directory(entry_path)
    except BaseException:
        if moved and not storage_path.exists():
            quarantine.quarantined_path.rename(storage_path)
            _fsync_directory(storage_path.parent)
        shutil.rmtree(entry_path, ignore_errors=True)
        _fsync_directory(quarantine_root)
        raise

    return quarantine


def restore_quarantined_native_repository(quarantine: NativeRepositoryQuarantine) -> None:
    persisted = _read_quarantine_entry(quarantine.entry_path)
    if persisted != quarantine:
        raise RuntimeError("Invalid native repository quarantine entry")
    if not quarantine.quarantined_path.exists():
        _delete_quarantine_entry(quarantine)
        return
    if quarantine.original_path.exists():
        raise RuntimeError("Cannot restore native repository from quarantine")

    quarantine.quarantined_path.rename(quarantine.original_path)
    _fsync_directory(quarantine.original_path.parent)
    _fsync_directory(quarantine.entry_path)
    _delete_quarantine_entry(quarantine)


def delete_quarantined_native_repository(quarantine: NativeRepositoryQuarantine) -> None:
    persisted = _read_quarantine_entry(quarantine.entry_path)
    if persisted != quarantine:
        raise RuntimeError("Invalid native repository quarantine entry")
    _delete_quarantine_entry(quarantine)


def reconcile_native_repository_quarantine() -> tuple[int, int]:
    from drastic_server.models.repository import Repository

    quarantine_root = _native_repository_quarantine_root(create=False)
    if quarantine_root is None:
        return 0, 0

    repository_paths = {
        repository.repository_path
        for repository in Repository.query.filter(Repository.kind == Repository.KIND_NATIVE).all()
        if repository.repository_path is not None
    }
    restored = 0
    deleted = 0

    for entry_path in quarantine_root.iterdir():
        try:
            quarantine = _read_quarantine_entry(entry_path)
            original_path = quarantine.original_path
            repository_path = original_path.relative_to(_native_repository_data_root()).as_posix()

            if repository_path not in repository_paths:
                delete_quarantined_native_repository(quarantine)
                deleted += 1
            elif not original_path.exists():
                restore_quarantined_native_repository(quarantine)
                restored += 1
            elif not quarantine.quarantined_path.exists():
                delete_quarantined_native_repository(quarantine)
                deleted += 1
            else:
                current_app.logger.error(
                    "Native repository quarantine conflicts with existing path: %s",
                    repository_path,
                )
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            current_app.logger.error(
                "Could not reconcile native repository quarantine entry %s (%s)",
                entry_path.name,
                type(exc).__name__,
            )

    return restored, deleted


def _normalize_native_repository_path(repository_path: str) -> str:
    raw_path = str(repository_path or "").strip()
    if not raw_path or Path(raw_path).is_absolute():
        raise RuntimeError("Invalid native repository path")

    try:
        normalized_path = normalize_restic_repository_path(raw_path)
    except ValueError as exc:
        raise RuntimeError("Invalid native repository path") from exc
    path_segments = normalized_path.split("/")
    if normalized_path != raw_path or len(path_segments) < 2 or path_segments[0] != "native":
        raise RuntimeError("Invalid native repository path")
    return normalized_path


def _native_repository_data_root() -> Path:
    return Path(
        str(current_app.config.get("REST_SERVER_DATA_DIRECTORY") or "/data").strip() or "/data"
    ).resolve()


def _native_repository_quarantine_root(*, create: bool) -> Path | None:
    quarantine_root = _native_repository_data_root() / ".quarantine"
    if create:
        quarantine_root.mkdir(mode=0o700, exist_ok=True)
    elif not quarantine_root.exists():
        return None

    if (
        quarantine_root.is_symlink()
        or not quarantine_root.is_dir()
        or quarantine_root.resolve() != quarantine_root
    ):
        raise RuntimeError("Invalid native repository quarantine path")
    return quarantine_root


def _write_quarantine_manifest(entry_path: Path, repository_path: str) -> None:
    manifest_path = entry_path / _QUARANTINE_MANIFEST
    temporary_path = entry_path / f".{_QUARANTINE_MANIFEST}.tmp"
    contents = json.dumps(
        {"version": _QUARANTINE_MANIFEST_VERSION, "original_path": repository_path},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as manifest_file:
            manifest_file.write(contents)
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    os.replace(temporary_path, manifest_path)
    _fsync_directory(entry_path)


def _read_quarantine_entry(entry_path: Path) -> NativeRepositoryQuarantine:
    quarantine_root = _native_repository_quarantine_root(create=False)
    if quarantine_root is None or entry_path.parent != quarantine_root:
        raise RuntimeError("Invalid native repository quarantine entry")
    try:
        entry_id = UUID(entry_path.name)
    except ValueError as exc:
        raise RuntimeError("Invalid native repository quarantine entry") from exc
    if entry_id.version != 4 or entry_id.hex != entry_path.name:
        raise RuntimeError("Invalid native repository quarantine entry")
    if entry_path.is_symlink() or not entry_path.is_dir() or entry_path.resolve() != entry_path:
        raise RuntimeError("Invalid native repository quarantine entry")

    manifest_path = entry_path / _QUARANTINE_MANIFEST
    descriptor = os.open(manifest_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        manifest_stat = os.fstat(descriptor)
        if not stat.S_ISREG(manifest_stat.st_mode) or manifest_stat.st_size > _MAX_MANIFEST_BYTES:
            raise RuntimeError("Invalid native repository quarantine manifest")
        with os.fdopen(descriptor, "rb") as manifest_file:
            descriptor = -1
            manifest = json.loads(manifest_file.read().decode("utf-8"))
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if not isinstance(manifest, dict) or manifest.get("version") != _QUARANTINE_MANIFEST_VERSION:
        raise RuntimeError("Invalid native repository quarantine manifest")
    repository_path = manifest.get("original_path")
    if not isinstance(repository_path, str):
        raise RuntimeError("Invalid native repository quarantine manifest")

    quarantined_path = entry_path / _QUARANTINE_PAYLOAD
    if quarantined_path.exists() and (
        quarantined_path.is_symlink()
        or not quarantined_path.is_dir()
        or quarantined_path.resolve() != quarantined_path
    ):
        raise RuntimeError("Invalid native repository quarantine payload")
    return NativeRepositoryQuarantine(
        original_path=native_repository_storage_path(repository_path),
        quarantined_path=quarantined_path,
    )


def _delete_quarantine_entry(quarantine: NativeRepositoryQuarantine) -> None:
    quarantine_root = _native_repository_quarantine_root(create=False)
    if quarantine_root is None or quarantine.entry_path.parent != quarantine_root:
        raise RuntimeError("Invalid native repository quarantine entry")
    shutil.rmtree(quarantine.entry_path)
    _fsync_directory(quarantine_root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
