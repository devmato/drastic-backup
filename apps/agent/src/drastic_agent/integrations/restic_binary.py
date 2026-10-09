"""Install a verified restic executable using an atomic replacement."""

import bz2
import hashlib
import logging
import os
import re
import stat
import subprocess
import tempfile
from zipfile import ZipFile

import requests

from drastic_agent.agent.exceptions import AgentExeption
from drastic_common.restic import RESTIC_VERSION


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
    archive_name = f"{name}.{'zip' if 'windows' in name else 'bz2'}"
    release_url = f"https://github.com/restic/restic/releases/download/v{RESTIC_VERSION}"
    archive_path = candidate_path = None
    try:
        descriptor, archive_path = tempfile.mkstemp(dir=folder, prefix=f".{archive_name}.", suffix=".tmp")
        with os.fdopen(descriptor, "wb") as archive:
            response = requests.get(f"{release_url}/{archive_name}", timeout=120)
            response.raise_for_status()
            archive.write(response.content)
        checksums = requests.get(f"{release_url}/SHA256SUMS", timeout=120)
        checksums.raise_for_status()
        expected = None
        for line in checksums.text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == archive_name:
                expected = parts[0].lower()
                break
        if not expected:
            raise AgentExeption(f"Restic checksum entry for {archive_name} not found in SHA256SUMS")
        with open(archive_path, "rb") as archive:
            actual = hashlib.file_digest(archive, "sha256").hexdigest()
        if actual != expected:
            raise AgentExeption(f"Restic binary checksum mismatch: expected {expected}, got {actual}")

        descriptor, candidate_path = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
        with os.fdopen(descriptor, "wb") as target:
            if archive_name.endswith(".bz2"):
                with bz2.open(archive_path, "rb") as source:
                    target.write(source.read())
            else:
                with ZipFile(archive_path) as archive:
                    members = [member for member in archive.filelist if not member.is_dir()]
                    if not members:
                        raise AgentExeption("Restic archive did not contain a binary")
                    with archive.open(members[0]) as source:
                        target.write(source.read())
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
        for temporary_path in (candidate_path, archive_path):
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
