import json
import os
import re
import ssl
from contextlib import closing
from pathlib import Path, PurePosixPath
from threading import Lock
from time import monotonic

import websocket
from marshmallow import ValidationError

from drastic_common import diagnostics
from drastic_common.truenas import validate_dataset

# ponytail: one TrueNAS job at a time on this agent; use per-connection admission
# if multiple NAS connections per agent are introduced.
TRUENAS_LOCK = Lock()


class TrueNASError(Exception):
    pass


def mount_source(path):
    """Require an actual ZFS mount, not an empty directory left by Docker."""
    target = str(Path(path).resolve())
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields, fs = line.split(" - ", 1)
            mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields.split()[4])
            filesystem, source, *_ = fs.split()
            if mount == target and filesystem == "zfs":
                return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), source)
    except (OSError, ValueError, IndexError):
        pass
    return None


class TrueNASClient:
    def __init__(self, settings, api_key="", timeout=15):
        self.settings = settings
        self.api_key = api_key
        self.timeout = timeout
        diagnostics.remember_secrets({"api_key": api_key})

    @property
    def public_settings(self):
        return {**self.settings, "api_key_configured": bool(self.api_key), "configured": bool(self.api_key)}

    def call(self, method, *params):
        if not self.api_key:
            raise TrueNASError("Configure the TrueNAS connection on this agent first")
        url = self.settings["api_url"].replace("https://", "wss://", 1) + "/api/current"
        deadline = monotonic() + self.timeout
        try:
            with closing(websocket.create_connection(
                url, timeout=self.timeout,
                sslopt={"cert_reqs": ssl.CERT_REQUIRED if self.settings["verify_tls"] else ssl.CERT_NONE},
                redirect_limit=0,
            )) as connection:

                def request(request_id, name, args):
                    connection.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": name, "params": args}))
                    while True:
                        remaining = deadline - monotonic()
                        if remaining <= 0:
                            raise TrueNASError("TrueNAS request timed out; its outcome may be unknown")
                        connection.settimeout(remaining)
                        response = json.loads(connection.recv())
                        if not isinstance(response, dict):
                            raise TrueNASError("Invalid TrueNAS JSON-RPC response")
                        if response.get("id") != request_id:
                            continue
                        if "error" in response:
                            error = response["error"]
                            reason = (error.get("data") or {}).get("reason") or error.get("message")
                            raise TrueNASError(f"TrueNAS {name}: {reason}")
                        return response.get("result")

                login = request(1, "auth.login_ex", [{
                    "mechanism": "API_KEY_PLAIN", "username": self.settings["username"],
                    "api_key": self.api_key, "login_options": {"user_info": False},
                }])
                if not isinstance(login, dict) or login.get("response_type") != "SUCCESS":
                    raise TrueNASError("TrueNAS authentication failed; check the user and API key")
                return request(2, method, list(params))
        except (OSError, ValueError, TypeError, websocket.WebSocketException, TrueNASError) as exc:
            raise TrueNASError(str(exc).replace(self.api_key, "<redacted>")) from None

    def version(self):
        version = str(self.call("system.version"))
        match = re.search(r"(\d{2})\.(\d{2})", version)
        if not match or tuple(map(int, match.groups())) < (25, 10):
            raise TrueNASError("TrueNAS 25.10 or newer is required")
        return version

    def test(self):
        return {"version": self.version(), "dataset_count": len(self.datasets())}

    def local_path(self, mountpoint):
        path = PurePosixPath(mountpoint or "")
        if not path.is_relative_to("/mnt") or ".." in path.parts:
            raise TrueNASError("Dataset has no supported /mnt mountpoint")
        root = Path(self.settings["host_root"])
        local = root / str(path).lstrip("/")
        if not local.resolve().is_relative_to(root.resolve()):
            raise TrueNASError("Dataset mount escapes the configured host root")
        return local

    def datasets(self):
        result = []
        for item in self.call("pool.dataset.query", [], {"extra": {"flat": True}}):
            name = item["id"]
            try:
                validate_dataset(name)
            except ValidationError:
                continue
            if item.get("type") != "FILESYSTEM":
                continue
            mountpoint = item.get("mountpoint")
            if isinstance(mountpoint, dict):
                mountpoint = mountpoint.get("value")
            error = "Dataset is locked" if item.get("locked") else ""
            local = None
            if not error:
                try:
                    local = self.local_path(mountpoint)
                    if mount_source(local) != name or not os.access(local, os.R_OK | os.X_OK):
                        error = "Dataset must be mounted read-only into this agent at its host-root path"
                except TrueNASError as exc:
                    error = str(exc)
            result.append({"id": name, "mountpoint": mountpoint, "path": str(local) if local else None,
                           "available": not error, "error": error})
        return sorted(result, key=lambda item: item["id"])

    def snapshot_path(self, dataset, name):
        host_path = str(PurePosixPath(dataset["mountpoint"]) / ".zfs/snapshot" / name)
        # Trigger ZFS automount in the host namespace; rslave makes it visible to Docker.
        self.call("filesystem.listdir", host_path, [], {"limit": 1, "select": ["name"]})
        local = self.local_path(host_path)
        if mount_source(local) != f"{dataset['id']}@{name}":
            raise TrueNASError("Snapshot is not mounted in the agent. Check read-only rslave bind mounts and host mount propagation")
        if not os.access(local, os.R_OK | os.X_OK):
            raise TrueNASError("Snapshot is not readable by the agent")
        return local
