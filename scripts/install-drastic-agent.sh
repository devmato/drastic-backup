#!/usr/bin/env bash
set -euo pipefail

ROOT=/opt/drastic-agent
REPOSITORY=${DRASTIC_AGENT_GIT_REPOSITORY:-https://github.com/devmato/drastic-backup.git}
REF=${DRASTIC_AGENT_REF:-}
SERVER=${DRASTIC_SERVER:-}
SOURCE=
REVISION=
REUSE_DATA=false
ARGS=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --repository|--ref|--server|--source)
            [ "$#" -ge 2 ] && [ -n "$2" ] || { printf 'Missing value for %s\n' "$1" >&2; exit 1; }
            case "$1" in
                --repository) REPOSITORY=$2 ;;
                --ref) REF=$2 ;;
                --server) SERVER=$2 ;;
                --source) SOURCE=$2 ;;
            esac
            shift 2 ;;
        --reuse-data) REUSE_DATA=true; shift ;;
        -h|--help)
            printf '%s\n' 'Usage: install-drastic-agent.sh [--server URL] [--repository URL] [--ref BRANCH|TAG|COMMIT] [--source CHECKOUT] [--user USER] [--password PASSWORD] [--reuse-data]'
            exit 0 ;;
        *) ARGS+=("$1"); shift ;;
    esac
done

root() {
    if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

case "$(uname -s)" in
    Linux) SERVICE_COMMAND=systemctl ;;
    Darwin)
        SERVICE_COMMAND=launchctl
        xcode-select -p >/dev/null 2>&1 || {
            printf 'Git requires Apple Command Line Tools. Run xcode-select --install, then retry.\n' >&2
            exit 1
        } ;;
    *) printf 'Linux with systemd or macOS is required.\n' >&2; exit 1 ;;
esac
for command in curl git "$SERVICE_COMMAND"; do
    command -v "$command" >/dev/null || { printf '%s is required.\n' "$command" >&2; exit 1; }
done
git --version >/dev/null
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null || { printf 'Root or sudo is required.\n' >&2; exit 1; }
    sudo -v
fi

MANAGED_BEFORE=false
DATA_BEFORE=false
if root test -f "$ROOT/.managed-by-drastic-agent"; then MANAGED_BEFORE=true; fi
if root test -e "$ROOT/data"; then DATA_BEFORE=true; fi

# Check ownership before downloading or writing the private runtime.
root bash -s -- "$REUSE_DATA" <<'CHECK'
set -euo pipefail
root=/opt/drastic-agent
fail() { printf '%s\n' "$*" >&2; exit 1; }
service=/etc/systemd/system/drastic-agent.service
if [ "$(uname -s)" = Darwin ]; then service=/Library/LaunchDaemons/net.drastic.agent.plist; fi
[ ! -L /opt ] && [ ! -L "$root" ] || fail 'Installation paths must not be symlinks.'
if [ -e "$root/agentctl" ]; then
    fail 'Legacy installation found. Run sudo drastic-agent uninstall (without --purge), then run this installer again. The agent identity will be retained.'
fi
if [ -e "$root" ]; then
    [ -d "$root" ] || fail 'Installation path must be a directory.'
    if [ "$(uname -s)" = Darwin ]; then
        [ "$(stat -f %u "$root")" = 0 ] || fail 'Installation directory must be owned by root.'
    else
        [ "$(stat -c %u "$root")" = 0 ] || fail 'Installation directory must be owned by root.'
    fi
    [ ! -L "$root/.managed-by-drastic-agent" ] || fail 'Invalid installation marker.'
fi
if [ ! -f "$root/.managed-by-drastic-agent" ]; then
    [ ! -e "$root/data" ] || [ "$1" = true ] || fail 'Unmanaged agent data found. Stop the old agent and use --reuse-data to retain its identity.'
    shopt -s dotglob nullglob
    for path in "$root"/*; do
        [ "$path" = "$root/data" ] && [ -d "$path" ] && [ ! -L "$path" ] || fail 'Installation directory is not empty or managed.'
    done
    [ ! -e /usr/local/bin/drastic-agent ] && [ ! -L /usr/local/bin/drastic-agent ] &&
        [ ! -e "$service" ] && [ ! -L "$service" ] || fail 'An unmanaged command or service already exists.'
fi
for path in tools tools/bin tools/python tools/restic cache; do
    [ ! -L "$root/$path" ] || fail 'Private runtime paths must not be symlinks.'
done
if [ "$(uname -s)" = Darwin ]; then
    # mkdir -p would otherwise create a missing /opt with the caller's private umask.
    if [ ! -e /opt ]; then mkdir -m 755 /opt; fi
    [ -d /opt ] || fail '/opt must be a directory.'
    mode=$(stat -f %Lp /opt)
    [ "$((8#$mode & 5))" -eq 5 ] || fail '/opt blocks the Full Disk Access picker. Grant read/traverse access to /opt, then retry (sudo chmod o+rx /opt).'
fi
mkdir -p "$root"
chmod 700 "$root"
# The macOS permission picker must be able to reach executables; data stays private.
if [ "$(uname -s)" = Darwin ]; then chmod 755 "$root"; fi
touch "$root/.managed-by-drastic-agent"
CHECK

STAGE=
INSTALLED=false
cleanup() {
    status=$?
    [ -z "$STAGE" ] || rm -rf "$STAGE"
    if [ "$status" -ne 0 ] && [ "$MANAGED_BEFORE" = false ] && [ "$INSTALLED" = false ]; then
        if ! root bash -s -- "$DATA_BEFORE" <<'CLEANUP'
set -euo pipefail
root=/opt/drastic-agent
service=/etc/systemd/system/drastic-agent.service
if [ "$(uname -s)" = Darwin ]; then
    service=/Library/LaunchDaemons/net.drastic.agent.plist
    [ "$(stat -f %u "$root")" = 0 ] || exit 0
else
    [ "$(stat -c %u "$root")" = 0 ] || exit 0
fi
[ -d "$root" ] && [ ! -L "$root" ] &&
    [ -f "$root/.managed-by-drastic-agent" ] && [ ! -L "$root/.managed-by-drastic-agent" ] || exit 0
# An active or partly committed installation must stay available for recovery.
service_active() {
    if [ "$(uname -s)" = Darwin ]; then
        launchctl print system/net.drastic.agent >/dev/null 2>&1
    else
        systemctl is-active --quiet drastic-agent.service
    fi
}
if service_active ||
    [ -e "$root/stopping.pid" ] || [ -L "$root/stopping.pid" ] ||
    [ -e "$root/current" ] || [ -L "$root/current" ] ||
    [ -e "$root/install.json" ] || [ -e "$root/drastic-agent.env" ] ||
    [ -e /usr/local/bin/drastic-agent ] || [ -L /usr/local/bin/drastic-agent ] ||
    [ -e "$service" ] || [ -L "$service" ]; then
    exit 0
fi
for path in tools cache releases bin data .installer.lock stopping.pid; do
    [ ! -L "$root/$path" ] || exit 0
done
rm -rf "$root/tools" "$root/cache" "$root/releases" "$root/bin"
if [ "$1" = true ] || { [ -d "$root/data" ] && [ -n "$(ls -A "$root/data")" ]; }; then
    exit 0  # Keep existing data or newly registered credentials and the marker for a retry.
fi
rmdir "$root/data" 2>/dev/null || true
rm -f "$root/.installer.lock"
rm "$root/.managed-by-drastic-agent"
rmdir "$root" 2>/dev/null || true
CLEANUP
        then
            printf 'Could not clean up incomplete agent installation at %s.\n' "$ROOT" >&2
        fi
    fi
    exit "$status"
}
trap cleanup EXIT
STAGE=$(mktemp -d)
trap 'exit 130' INT
trap 'exit 143' TERM
UV=$ROOT/tools/bin/uv
if ! root test -x "$UV"; then
    curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/0.10.9/install.sh -o "$STAGE/uv.sh"
    root env UV_UNMANAGED_INSTALL="$ROOT/tools/bin" XDG_CONFIG_HOME="$ROOT/tools/config" bash "$STAGE/uv.sh"
fi
root env UV_PYTHON_INSTALL_DIR="$ROOT/tools/python" UV_PYTHON_INSTALL_BIN=false UV_CACHE_DIR="$ROOT/cache/uv" "$UV" python install 3.11
PYTHON=$(root env UV_PYTHON_INSTALL_DIR="$ROOT/tools/python" UV_CACHE_DIR="$ROOT/cache/uv" "$UV" python find --managed-python 3.11)

if [ -z "$SOURCE" ] && [ -z "$REF" ]; then
    if [ -z "$SERVER" ]; then
        if (: </dev/tty) 2>/dev/null; then
            printf 'Server URL: ' >&2
            read -r SERVER </dev/tty
        fi
        [ -n "$SERVER" ] || { printf 'Provide --server URL for unattended installation.\n' >&2; exit 1; }
    fi
    SERVER=${SERVER%/}
    REVISION=$(curl --fail --silent --show-error --max-time 30 --proto '=https,http' \
        -- "$SERVER/api/agents/installation-target" | root "$PYTHON" -c '
import json, re, sys
try:
    target = json.loads(sys.stdin.read(4097))
    revision, version = target["revision"], target["version"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid revision")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-" + revision[:8], version):
        raise ValueError("Invalid version")
except (ValueError, KeyError, TypeError):
    raise SystemExit("The backend has no reproducible agent installation target; use a published build or --source for local development.")
print(revision)
')
fi
[ "${REF#-}" = "$REF" ] || { printf 'Invalid Git ref.\n' >&2; exit 1; }
if [ -z "$SOURCE" ]; then
    if [ -n "$REVISION" ]; then
        git init --quiet "$STAGE/source"
        git -C "$STAGE/source" remote add -- origin "$REPOSITORY"
        GIT_TERMINAL_PROMPT=0 git -C "$STAGE/source" fetch --depth=1 --no-tags origin "$REVISION"
        [ "$(git -C "$STAGE/source" rev-parse 'FETCH_HEAD^{commit}')" = "$REVISION" ] || { printf 'Source does not match the backend commit.\n' >&2; exit 1; }
        git -C "$STAGE/source" checkout --detach "$REVISION"
    else
        git clone --no-checkout -- "$REPOSITORY" "$STAGE/source"
        CHECKOUT_REF=$REF
        if git -C "$STAGE/source" show-ref --verify --quiet "refs/remotes/origin/$REF"; then
            CHECKOUT_REF="refs/remotes/origin/$REF"
        fi
        git -C "$STAGE/source" checkout --detach "$CHECKOUT_REF"
    fi
    SOURCE=$STAGE/source
fi

# Old agent sources must not bootstrap their ref-pinned lifecycle manager.
MANAGER=$SOURCE/scripts/drastic-agent-installer.py
if ! root "$PYTHON" -c '
from pathlib import Path
import sys
script = Path(sys.argv[1])
sys.exit(0 if script.is_file() and "\nINDEPENDENT_LIFECYCLE = True\n" in script.read_text() else 1)
' "$MANAGER"; then
    if [ -z "$SERVER" ] && (: </dev/tty) 2>/dev/null; then
        printf 'Server URL: ' >&2
        read -r SERVER </dev/tty
    fi
    [ -n "$SERVER" ] || { printf 'Provide --server URL to obtain a compatible lifecycle manager.\n' >&2; exit 1; }
    SERVER=${SERVER%/}
    MANAGER=$STAGE/installer.py
    curl --fail --silent --show-error --max-time 30 --proto '=https,http' \
        -o "$MANAGER" -- "$SERVER/install.py"
fi
if [ "$(uname -s)" = Darwin ] && ! root "$PYTHON" -c '
from pathlib import Path
import sys
sys.exit(0 if "\nMACOS_LIFECYCLE = True\n" in Path(sys.argv[1]).read_text() else 1)
' "$MANAGER"; then
    printf 'This installer version does not support macOS. Use a macOS-capable backend or test branch.\n' >&2
    exit 1
fi
COMMAND=("$PYTHON" "$MANAGER" install --source "$SOURCE" --repository "$REPOSITORY")
[ -z "$REVISION" ] || COMMAND+=(--revision "$REVISION")
[ -z "$REF" ] || COMMAND+=(--ref "$REF")
[ -z "$SERVER" ] || COMMAND+=(--server "$SERVER")
# The pipe is consumed by bash; prompts must use the terminal instead.
if (: </dev/tty) 2>/dev/null; then
    root "${COMMAND[@]}" ${ARGS[@]+"${ARGS[@]}"} </dev/tty
else
    root "${COMMAND[@]}" ${ARGS[@]+"${ARGS[@]}"}
fi
INSTALLED=true
if [ "$(uname -s)" = Darwin ] && [ "$(id -u)" -ne 0 ]; then
    open 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles' || true
fi
