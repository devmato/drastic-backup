import os
import platform
import re
import subprocess

import requests

from drastic_agent.config import DefaultConfig, env_flag, env_int, env_value
from drastic_common import diagnostics


class ProxmoxError(Exception):
    pass


class ProxmoxApiClient:
    def __init__(self, settings=None):
        self.base_url = env_value("DRASTIC_PROXMOX_API_URL", DefaultConfig.PROXMOX_API_URL).rstrip(
            "/"
        )
        self.token_id = str(os.environ.get("DRASTIC_PROXMOX_TOKEN_ID") or "").strip()
        self.token_secret = str(os.environ.get("DRASTIC_PROXMOX_TOKEN_SECRET") or "").strip()
        self.node_override = str(os.environ.get("DRASTIC_PROXMOX_NODE") or "").strip()
        self.verify_tls = env_flag("DRASTIC_PROXMOX_VERIFY_TLS", DefaultConfig.PROXMOX_VERIFY_TLS)
        self.source = "environment"
        if settings is not None:
            self.base_url = settings["api_url"]
            self.token_id = settings["token_id"]
            self.token_secret = settings["token_secret"]
            self.node_override = settings["node"]
            self.verify_tls = settings["verify_tls"]
            self.source = "agent"
        self.command_timeout = env_int(
            "DRASTIC_COMMAND_TIMEOUT_SECONDS", DefaultConfig.COMMAND_TIMEOUT_SECONDS
        )
        self._node = None
        diagnostics.remember_secrets({"token_secret": self.token_secret})

    @property
    def configured(self):
        return bool(self.token_id and self.token_secret)

    @property
    def public_settings(self):
        return {
            "api_url": self.base_url,
            "token_id": self.token_id,
            "node": self.node_override,
            "verify_tls": self.verify_tls,
            "token_secret_configured": bool(self.token_secret),
            "configured": self.configured,
            "source": self.source,
        }

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
            "Unable to determine local Proxmox node automatically. Set the node in the agent's Proxmox settings or DRASTIC_PROXMOX_NODE."
        )

    def list_qemu_guests(self):
        node = self.get_node()
        guests = self._request("GET", f"/nodes/{node}/qemu") or []

        return sorted(guests, key=lambda guest: guest.get("vmid", 0))

    def get_qemu_config(self, vmid):
        node = self.get_node()
        return self._request("GET", f"/nodes/{node}/qemu/{vmid}/config") or {}


class QemuVolumeGuestDriver:
    _QEMU_VOLUME_KEYS = re.compile(r"^(?:ide|sata|scsi|virtio)\d+$|^(?:efidisk0|tpmstate0)$")
    _BACKUP_PROGRESS = re.compile(
        r"(?:INFO:\s*)?(\d+(?:\.\d+)?)%\s*\((\d+(?:\.\d+)?)\s*([KMGTPE]?i?B) of (\d+(?:\.\d+)?)\s*([KMGTPE]?i?B)\)"
    )

    @classmethod
    def parse_backup_progress(cls, line):
        match = cls._BACKUP_PROGRESS.search(line)
        if not match:
            return None
        percent, done, done_unit, total, total_unit = match.groups()

        def to_bytes(value, unit):
            exponent = "BKMGTPE".index(unit[0])
            return int(float(value) * (1024 if "i" in unit else 1000) ** exponent)

        total_bytes = to_bytes(total, total_unit)
        if total_bytes <= 0 or not 0 <= float(percent) <= 100:
            return None
        return {
            "percent_done": float(percent),
            "bytes_processed": to_bytes(done, done_unit),
            "bytes_total": total_bytes,
        }

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
        progress_callback=None,
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

        def process_stderr(line):
            diagnostics.record("proxmox.output", {"vmid": vmid, "line": line},
                               operation_uuid=getattr(getattr(callback, "__self__", None), "uuid", None))
            progress = self.parse_backup_progress(line)
            if progress is not None and progress_callback is not None:
                progress_callback(progress)

        return resticapi.backup_stdin_from_command(
            command=command,
            stdin_filename=stdin_filename,
            tags=tags,
            callback=callback,
            callback_args=callback_args,
            callback_pid=callback_pid,
            callback_throttle=callback_throttle,
            producer_stderr_callback=process_stderr if progress_callback else None,
        )


def ensure_vzdump_available():
    try:
        subprocess.run(["vzdump", "help"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ProxmoxError(
            "vzdump is not available on this agent. Run the agent directly on the Proxmox host."
        ) from exc
