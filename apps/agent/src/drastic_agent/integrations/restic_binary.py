"""Install a verified restic executable using an atomic replacement."""

import logging
import os
import re
import stat
import subprocess
import tempfile

import requests

from drastic_agent.agent.exceptions import AgentExeption
from drastic_common.restic import RESTIC_VERSION
from drastic_common.restic.binary import download_binary


def is_valid(path):
    if not os.path.isfile(path) or not os.access(path, os.X_OK):
        return False
    try:
        result = subprocess.run([path, "version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and re.search(
        rf"\brestic\s+{re.escape(RESTIC_VERSION)}(?:\s|$)", f"{result.stdout}\n{result.stderr}"
    ) is not None


def ensure_binary(folder, platform, arch):
    name = f"restic_{RESTIC_VERSION}_{platform}_{arch}"
    path = os.path.join(folder, name)
    os.makedirs(folder, exist_ok=True)
    if not is_valid(path):
        download(folder, name, path)
    return path


def download(folder, name, path):
    candidate_path = None
    try:
        descriptor, candidate_path = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
        with os.fdopen(descriptor, "wb") as target:
            download_binary(name, target, get=requests.get)
        mode = os.stat(candidate_path).st_mode
        os.chmod(candidate_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if not is_valid(candidate_path):
            raise AgentExeption(f"Downloaded restic binary failed version check for {RESTIC_VERSION}")
        # An interrupted download must never replace the last working executable.
        os.replace(candidate_path, path)
        candidate_path = None
        logging.info("Downloaded restic version %s to %s", RESTIC_VERSION, path)
    except Exception as exc:
        raise AgentExeption(f"Restic binary download failed: {exc}") from exc
    finally:
        if candidate_path:
            try:
                os.unlink(candidate_path)
            except FileNotFoundError:
                pass
