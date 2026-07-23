#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHON_BINARY=${PYTHON_BINARY:-python3}
VERSION=$("$PYTHON_BINARY" -c 'import runpy, sys; print(runpy.run_path(sys.argv[1])["RESTIC_VERSION"])' \
    "$ROOT_DIR/libs/python/common/src/drastic_common/restic/constants.py")
INSTALL_PATH=${RESTIC_INSTALL_PATH:-/usr/local/bin/restic}

case "$(uname -m)" in
    x86_64|amd64)
        ARCH=amd64
        ;;
    aarch64|arm64)
        ARCH=arm64
        ;;
    *)
        echo "Unsupported CI architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

ARCHIVE_NAME="restic_${VERSION}_linux_${ARCH}.bz2"
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT
CANDIDATE_PATH="$TEMP_DIR/restic"

"$PYTHON_BINARY" - "$VERSION" "$ARCHIVE_NAME" "$CANDIDATE_PATH" <<'PY'
import bz2
import hashlib
import sys
from pathlib import Path
from urllib.request import urlopen

version, archive_name, candidate_path = sys.argv[1:]
base_url = f"https://github.com/restic/restic/releases/download/v{version}"

with urlopen(f"{base_url}/SHA256SUMS", timeout=120) as response:
    checksum_lines = response.read().decode("utf-8").splitlines()

expected_checksum = None
for line in checksum_lines:
    parts = line.split()
    if len(parts) == 2 and parts[1].lstrip("*") == archive_name:
        expected_checksum = parts[0].lower()
        break

if expected_checksum is None:
    raise SystemExit(f"Checksum for {archive_name} is missing")

with urlopen(f"{base_url}/{archive_name}", timeout=120) as response:
    archive = response.read()

actual_checksum = hashlib.sha256(archive).hexdigest()
if actual_checksum != expected_checksum:
    raise SystemExit(
        f"Checksum mismatch for {archive_name}: expected {expected_checksum}, got {actual_checksum}"
    )

Path(candidate_path).write_bytes(bz2.decompress(archive))
PY

if [[ -w "$(dirname "$INSTALL_PATH")" ]]; then
    install -m 0755 "$CANDIDATE_PATH" "$INSTALL_PATH"
else
    sudo install -m 0755 "$CANDIDATE_PATH" "$INSTALL_PATH"
fi

VERSION_OUTPUT=$("$INSTALL_PATH" version)
if [[ "$VERSION_OUTPUT" != "restic $VERSION "* ]]; then
    echo "Installed restic version is invalid: $VERSION_OUTPUT" >&2
    exit 1
fi

echo "$VERSION_OUTPUT"
