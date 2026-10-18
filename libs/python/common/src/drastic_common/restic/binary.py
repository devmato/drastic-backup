"""Download and unpack checksum-verified Restic releases without executing them."""

import bz2
import hashlib
import re
import shutil
from io import BytesIO
from time import monotonic
from zipfile import ZipFile

from drastic_common.restic.constants import RESTIC_VERSION

RECOVERY_TARGETS = ("linux_amd64", "linux_arm64", "darwin_amd64", "darwin_arm64", "windows_amd64")
RELEASE_URL = f"https://github.com/restic/restic/releases/download/v{RESTIC_VERSION}"


def _download_before_deadline(url, get, deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Restic download deadline exceeded")
    # Short I/O waits leave room to report a total-budget failure before the proxy times out.
    with get(url, timeout=min(10, remaining), stream=True, headers={"Accept-Encoding": "identity"}) as response:
        response.raise_for_status()
        response.raw.decode_content = True
        content = BytesIO()
        while True:
            if monotonic() >= deadline:
                raise TimeoutError("Restic download deadline exceeded")
            # read1 returns available bytes instead of waiting to fill a chunk on a slow stream.
            chunk = response.raw.read1(64 * 1024)
            if monotonic() >= deadline:
                raise TimeoutError("Restic download deadline exceeded")
            if not chunk:
                return content.getvalue()
            content.write(chunk)


def download_archive(name, *, get, deadline=None):
    """Return the official archive's filename, unchanged bytes and verified SHA-256.

    An absolute monotonic deadline shares a streaming budget across recovery downloads;
    agent installation keeps its existing per-request timeout when no deadline is supplied.
    """
    if not re.fullmatch(rf"restic_{re.escape(RESTIC_VERSION)}_[a-z0-9]+_[a-z0-9]+", name):
        raise ValueError("Invalid Restic release name")
    archive_name = f"{name}.{'zip' if '_windows_' in name else 'bz2'}"
    if deadline is None:
        response = get(f"{RELEASE_URL}/{archive_name}", timeout=120)
        response.raise_for_status()
        content = response.content
        checksums = get(f"{RELEASE_URL}/SHA256SUMS", timeout=120)
        checksums.raise_for_status()
        checksum_text = checksums.text
    else:
        content = _download_before_deadline(f"{RELEASE_URL}/{archive_name}", get, deadline)
        checksum_text = _download_before_deadline(f"{RELEASE_URL}/SHA256SUMS", get, deadline).decode("utf-8")
    expected = next((parts[0].lower() for line in checksum_text.splitlines()
                     if len(parts := line.split()) == 2 and parts[1].lstrip("*") == archive_name), None)
    if not expected:
        raise ValueError(f"Restic checksum entry for {archive_name} not found in SHA256SUMS")
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ValueError(f"Restic binary checksum mismatch: expected {expected}, got {actual}")
    return archive_name, content, actual


def download_binary(name, target, *, get):
    """Unpack a verified release into an open binary file; the caller owns activation."""
    archive_name, content, _ = download_archive(name, get=get)
    with BytesIO(content) as compressed:
        if archive_name.endswith(".bz2"):
            with bz2.open(compressed, "rb") as source:
                shutil.copyfileobj(source, target)
        else:
            with ZipFile(compressed) as archive:
                members = [member for member in archive.filelist if not member.is_dir()]
                if len(members) != 1 or not members[0].filename.endswith(".exe"):
                    raise ValueError("Restic archive did not contain exactly one executable")
                with archive.open(members[0]) as source:
                    shutil.copyfileobj(source, target)
