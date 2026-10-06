"""Guest-file platform boundary; Linux dependencies are loaded only by the Linux backend."""

import sys
from typing import Protocol


class GuestFileBackend(Protocol):
    def check_available(self) -> None: ...

    def request(self, resticapi, descriptor, work, action, *, cancelled, **kwargs) -> dict: ...

    def lock_workspace(self, fd: int) -> None: ...

    def cleanup_workspace(self, path) -> None: ...


def get_guest_file_backend() -> GuestFileBackend:
    if sys.platform == "linux":
        from drastic_agent.services.guest_linux import LinuxGuestFileBackend

        return LinuxGuestFileBackend()
    raise ValueError(f"Guest file restore is not implemented for {sys.platform}")
