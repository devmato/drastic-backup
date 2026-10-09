"""Explicit worker capabilities, excluding sockets, schedulers and runtime internals."""

from collections.abc import Callable
from dataclasses import dataclass

from drastic_common.restic import ResticApi


@dataclass(frozen=True)
class BackupContext:
    identifier: str | int | None
    resticapi: ResticApi
    set_repository: Callable
    execute_actions: Callable
    initialize_repository_with_recovery: Callable
    recover_repository_access_with_recovery: Callable
    cmd_run_retention: Callable
    cmd_get_repository_stats: Callable
    get_proxmox_client: Callable
    get_truenas_client: Callable
