import os
import platform
import re
import subprocess
import time

import requests


class ProxmoxError(Exception):
    pass


class ProxmoxApiClient:
    def __init__(self):
        self.base_url = str(
            os.environ.get("DRASTIC_PROXMOX_API_URL") or "https://127.0.0.1:8006/api2/json"
        ).rstrip("/")
        self.token_id = str(os.environ.get("DRASTIC_PROXMOX_TOKEN_ID") or "").strip()
        self.token_secret = str(os.environ.get("DRASTIC_PROXMOX_TOKEN_SECRET") or "").strip()
        self.node_override = str(os.environ.get("DRASTIC_PROXMOX_NODE") or "").strip()
        self.verify_tls = str(
            os.environ.get("DRASTIC_PROXMOX_VERIFY_TLS") or "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.command_timeout = _env_int("DRASTIC_COMMAND_TIMEOUT_SECONDS", 60)
        self.task_timeout = _env_int("DRASTIC_TASK_TIMEOUT_SECONDS", 600)
        self._node = None

    @property
    def configured(self):
        return bool(self.token_id and self.token_secret)

    def _headers(self):
        if not self.configured:
            raise ProxmoxError("Proxmox API token is not configured on the agent")

        return {
            "Authorization": f"PVEAPIToken={self.token_id}={self.token_secret}",
        }

    def _request(self, method, path, params=None, data=None):
        try:
            response = requests.request(
                method=method,
                url=f"{self.base_url}{path}",
                headers=self._headers(),
                params=params,
                data=data,
                timeout=self.command_timeout,
                verify=self.verify_tls,
            )
        except requests.RequestException as exc:
            raise ProxmoxError(f"Failed to reach Proxmox API: {exc}") from exc

        payload = None
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code >= 400:
            if payload is not None:
                message = payload.get("errors") or payload.get("message") or payload
                raise ProxmoxError(f"Proxmox API request failed: {message}")

            body = response.text.strip()
            if body:
                raise ProxmoxError(f"Proxmox API request failed ({response.status_code}): {body}")
            raise ProxmoxError(f"Unexpected response from Proxmox API ({response.status_code})")

        if payload is None:
            body = response.text.strip()
            if not body:
                return None
            raise ProxmoxError(
                f"Unexpected response from Proxmox API ({response.status_code}): {body}"
            )

        return payload.get("data")

    def get_node(self):
        if self._node:
            return self._node

        if self.node_override:
            self._node = self.node_override
            return self._node

        nodes = self._request("GET", "/nodes") or []
        if len(nodes) == 1:
            self._node = nodes[0]["node"]
            return self._node

        hostname = platform.node().split(".")[0]
        for node in nodes:
            if node.get("node") == hostname:
                self._node = node["node"]
                return self._node

        raise ProxmoxError(
            "Unable to determine local Proxmox node automatically. Set DRASTIC_PROXMOX_NODE."
        )

    def list_qemu_guests(self):
        node = self.get_node()
        guests = self._request("GET", f"/nodes/{node}/qemu") or []

        return sorted(guests, key=lambda guest: guest.get("vmid", 0))

    def get_qemu_config(self, vmid):
        node = self.get_node()
        return self._request("GET", f"/nodes/{node}/qemu/{vmid}/config") or {}

    def create_qemu_snapshot(self, vmid, snapname):
        node = self.get_node()
        return self._request(
            "POST",
            f"/nodes/{node}/qemu/{vmid}/snapshot",
            data={"snapname": snapname, "vmstate": 0},
        )

    def delete_qemu_snapshot(self, vmid, snapname):
        node = self.get_node()
        return self._request(
            "DELETE",
            f"/nodes/{node}/qemu/{vmid}/snapshot/{snapname}",
            data={"force": 1},
        )

    def wait_for_task(self, upid, poll_interval=2, timeout=None):
        node = self.get_node()
        effective_timeout = timeout or self.task_timeout
        started = time.monotonic()

        while True:
            status = self._request("GET", f"/nodes/{node}/tasks/{upid}/status") or {}
            if status.get("status") == "stopped":
                if status.get("exitstatus") != "OK":
                    raise ProxmoxError(
                        f"Proxmox task failed with exit status {status.get('exitstatus')}"
                    )
                return status

            if time.monotonic() - started > effective_timeout:
                raise ProxmoxError(f"Timed out while waiting for Proxmox task {upid}")

            time.sleep(poll_interval)


def _env_int(name: str, default: int) -> int:
    try:
        parsed = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default

    return parsed if parsed > 0 else default


class ProxmoxGuestDriver:
    def list_supported_guests(self, api):
        raise NotImplementedError

    def get_guest_backup_plan(self, config):
        raise NotImplementedError

    def export_qemu_backup_to_restic(
        self,
        api,
        resticapi,
        vmid,
        stdin_filename,
        tags,
        callback,
        callback_args,
        callback_pid,
        callback_throttle,
    ):
        raise NotImplementedError


class QemuVolumeGuestDriver(ProxmoxGuestDriver):
    _QEMU_VOLUME_KEYS = re.compile(r"^(?:ide|sata|scsi|virtio)\d+$|^(?:efidisk0|tpmstate0)$")

    def _extract_volid(self, value):
        if not value:
            return None

        volid = str(value).split(",", 1)[0].strip()
        if not volid or ":" not in volid or volid.startswith("/"):
            return None

        return volid

    def get_guest_backup_plan(self, config):
        volumes = []

        for key, value in (config or {}).items():
            if not self._QEMU_VOLUME_KEYS.match(str(key)):
                continue

            value = str(value)
            if "media=cdrom" in value or "backup=0" in value:
                continue

            volid = self._extract_volid(value)
            if not volid:
                continue

            volumes.append(
                {
                    "disk": key,
                    "volume": volid,
                    "export_format": "raw+size",
                }
            )

        return {"volumes": volumes}

    def list_supported_guests(self, api):
        guests = []

        for guest in api.list_qemu_guests():
            vmid = guest.get("vmid")
            if vmid is None:
                continue

            config = api.get_qemu_config(vmid)
            plan = self.get_guest_backup_plan(config)
            if plan["volumes"]:
                guests.append(
                    {
                        "vmid": vmid,
                        "name": guest.get("name"),
                        "node": api.get_node(),
                        "status": guest.get("status"),
                        "type": "qemu",
                    }
                )

        return guests

    def export_qemu_backup_to_restic(
        self,
        api,
        resticapi,
        vmid,
        stdin_filename,
        tags,
        callback,
        callback_args,
        callback_pid,
        callback_throttle,
    ):
        command = [
            "vzdump",
            str(vmid),
            "--mode",
            "snapshot",
            "--stdout",
            "--compress",
            "0",
            "--node",
            api.get_node(),
        ]

        return resticapi.backup_stdin_from_command(
            command=command,
            stdin_filename=stdin_filename,
            tags=tags,
            callback=callback,
            callback_args=callback_args,
            callback_pid=callback_pid,
            callback_throttle=callback_throttle,
        )


def get_proxmox_guest_driver():
    return QemuVolumeGuestDriver()


def ensure_vzdump_available():
    try:
        subprocess.run(["vzdump", "help"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ProxmoxError(
            "vzdump is not available on this agent. Run the agent directly on the Proxmox host."
        ) from exc
