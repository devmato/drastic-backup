from __future__ import annotations

import shlex
import textwrap


def render_linux_agent_install_script(server_url: str) -> str:
    quoted_server_url = shlex.quote(server_url.rstrip("/"))
    script = r'''#!/usr/bin/env bash
set -euo pipefail

SERVER_URL=__SERVER_URL__
AGENT_ROOT=/opt/drastic-agent
REAL_BINARY="$AGENT_ROOT/bin/drastic-agent"
DATA_DIR="$AGENT_ROOT/data"
ENV_FILE="$AGENT_ROOT/drastic-agent.env"
WRAPPER=/usr/local/bin/drastic-agent
SERVICE_NAME=drastic-agent
SERVICE_FILE=/etc/systemd/system/drastic-agent.service

DRASTIC_USER_VALUE=${DRASTIC_USER:-}
DRASTIC_PASSWORD_VALUE=${DRASTIC_PASSWORD:-}
NO_START=false
UNINSTALL=false
PURGE=false
YES=false

usage() {
    cat <<'EOF'
dRastic Agent Installer

Usage:
  curl -fsSL https://drastic-url/install | bash
  curl -fsSL https://drastic-url/install | bash -s -- --user USER --password PASSWORD

Options:
  --user USER          dRastic username for initial agent registration
  --password PASSWORD  dRastic password for initial agent registration
  --no-start           Install and enable the service without starting it
  --uninstall          Uninstall the native agent service and binary
  --purge              Remove agent data during uninstall
  --yes                Skip confirmation prompts for destructive actions
  -h, --help           Show this help
EOF
}

info() {
    printf '[INFO] %s\n' "$*"
}

warn() {
    printf '[WARN] %s\n' "$*" >&2
}

fatal() {
    printf '[ERROR] %s\n' "$*" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --user)
            [ -n "${2:-}" ] || fatal '--user requires a value'
            DRASTIC_USER_VALUE=$2
            shift 2
            ;;
        --password)
            [ -n "${2:-}" ] || fatal '--password requires a value'
            DRASTIC_PASSWORD_VALUE=$2
            shift 2
            ;;
        --no-start)
            NO_START=true
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --purge)
            PURGE=true
            shift
            ;;
        --yes|-y)
            YES=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fatal "unknown option: $1"
            ;;
    esac
done

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || fatal "required command not found: $1"
}

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

run_root_shell() {
    if [ "$(id -u)" -eq 0 ]; then
        sh -c "$1"
    else
        sudo sh -c "$1"
    fi
}

ensure_root_runner() {
    if [ "$(id -u)" -eq 0 ]; then
        return
    fi
    command -v sudo >/dev/null 2>&1 || fatal 'this installer requires root or sudo'
}

run_uninstall() {
    ensure_root_runner
    if [ -x "$WRAPPER" ]; then
        args=(uninstall)
        [ "$PURGE" = true ] && args+=(--purge)
        [ "$YES" = true ] && args+=(--yes)
        run_root "$WRAPPER" "${args[@]}"
        exit 0
    fi
    fatal "native agent wrapper not found at $WRAPPER"
}

if [ "$UNINSTALL" = true ]; then
    run_uninstall
fi

[ "$(uname -s)" = Linux ] || fatal 'this installer currently supports Linux only'
need_cmd systemctl
ensure_root_runner
need_cmd tar
need_cmd mktemp
need_cmd chmod
need_cmd mkdir

if command -v curl >/dev/null 2>&1; then
    download() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
    download() { wget -qO "$2" "$1"; }
else
    fatal 'curl or wget is required'
fi

case "$(uname -m)" in
    x86_64|amd64) ARCH=amd64 ;;
    *) fatal "unsupported CPU architecture: $(uname -m)" ;;
esac

if [ -z "$DRASTIC_USER_VALUE" ]; then
    [ -r /dev/tty ] || fatal 'missing --user and no TTY is available for prompting'
    printf 'dRastic username: ' >/dev/tty
    IFS= read -r DRASTIC_USER_VALUE </dev/tty
fi

if [ -z "$DRASTIC_PASSWORD_VALUE" ]; then
    [ -r /dev/tty ] || fatal 'missing --password and no TTY is available for prompting'
    printf 'dRastic password: ' >/dev/tty
    stty -echo </dev/tty
    IFS= read -r DRASTIC_PASSWORD_VALUE </dev/tty
    stty echo </dev/tty
    printf '\n' >/dev/tty
fi

[ -n "$DRASTIC_USER_VALUE" ] || fatal 'username cannot be empty'
[ -n "$DRASTIC_PASSWORD_VALUE" ] || fatal 'password cannot be empty'

TMP_DIR=$(mktemp -d -t drastic-agent-install.XXXXXXXXXX)
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

ARTIFACT_URL="$SERVER_URL/agents/linux/$ARCH"
ARCHIVE="$TMP_DIR/drastic-agent.tar.gz"

info "Downloading agent artifact for linux/$ARCH"
download "$ARTIFACT_URL" "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$TMP_DIR"

EXTRACTED_BINARY=$(find "$TMP_DIR" -type f -name drastic-agent | head -n 1)
[ -n "$EXTRACTED_BINARY" ] || fatal 'downloaded artifact did not contain drastic-agent'
chmod 755 "$EXTRACTED_BINARY"

info "Installing agent binary to $REAL_BINARY"
run_root mkdir -p "$AGENT_ROOT/bin" "$DATA_DIR"
run_root install -m 0755 "$EXTRACTED_BINARY" "$REAL_BINARY"

WRAPPER_TMP="$TMP_DIR/drastic-agent-wrapper"
cat > "$WRAPPER_TMP" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

AGENT_ROOT=/opt/drastic-agent
REAL_BINARY="$AGENT_ROOT/bin/drastic-agent"
DATA_DIR="$AGENT_ROOT/data"
ENV_FILE="$AGENT_ROOT/drastic-agent.env"
WRAPPER=/usr/local/bin/drastic-agent
SERVICE_NAME=drastic-agent
SERVICE_FILE=/etc/systemd/system/drastic-agent.service

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

confirm_purge() {
    if [ "$YES" = true ]; then
        return
    fi
    [ -r /dev/tty ] || {
        echo 'Refusing to purge without a TTY. Re-run with --yes to confirm.' >&2
        exit 1
    }
    printf 'Remove all local dRastic agent data in %s? [y/N] ' "$DATA_DIR" >/dev/tty
    IFS= read -r answer </dev/tty
    case "$answer" in
        y|Y|yes|YES) ;;
        *) echo 'Purge aborted.'; exit 0 ;;
    esac
}

uninstall() {
    PURGE=false
    YES=false
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --purge) PURGE=true ;;
            --yes|-y) YES=true ;;
            --help|-h)
                cat <<'USAGE'
Usage:
  drastic-agent uninstall
  drastic-agent uninstall --purge
  drastic-agent uninstall --purge --yes
USAGE
                exit 0
                ;;
            *) echo "unknown uninstall option: $1" >&2; exit 1 ;;
        esac
        shift
    done

    if [ "$PURGE" = true ]; then
        confirm_purge
    fi

    if command -v systemctl >/dev/null 2>&1; then
        run_root systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
        run_root systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi

    run_root rm -f "$SERVICE_FILE" "$ENV_FILE" "$REAL_BINARY"

    if [ "$PURGE" = true ]; then
        run_root rm -rf "$DATA_DIR"
    fi

    run_root rmdir "$AGENT_ROOT/bin" >/dev/null 2>&1 || true
    run_root rmdir "$AGENT_ROOT" >/dev/null 2>&1 || true

    if command -v systemctl >/dev/null 2>&1; then
        run_root systemctl daemon-reload >/dev/null 2>&1 || true
        run_root systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi

    run_root rm -f "$WRAPPER"
    echo 'dRastic agent uninstalled.'
    if [ "$PURGE" != true ]; then
        echo "Kept agent data in $DATA_DIR. Re-run with --purge to remove it."
    fi
}

if [ "${1:-}" = uninstall ]; then
    shift
    uninstall "$@"
    exit 0
fi

exec "$REAL_BINARY" "$@"
EOF
run_root install -m 0755 "$WRAPPER_TMP" "$WRAPPER"

info "Registering agent with $SERVER_URL"
if [ "$(id -u)" -eq 0 ]; then
    DRASTIC_ENV=prod \
    DRASTIC_SERVER="$SERVER_URL" \
    DRASTIC_USER="$DRASTIC_USER_VALUE" \
    DRASTIC_PASSWORD="$DRASTIC_PASSWORD_VALUE" \
    DRASTIC_AGENT_DATA_DIR="$DATA_DIR" \
    "$REAL_BINARY" register
else
    sudo env \
        DRASTIC_ENV=prod \
        "DRASTIC_SERVER=$SERVER_URL" \
        "DRASTIC_USER=$DRASTIC_USER_VALUE" \
        "DRASTIC_PASSWORD=$DRASTIC_PASSWORD_VALUE" \
        "DRASTIC_AGENT_DATA_DIR=$DATA_DIR" \
        "$REAL_BINARY" register
fi

if ! run_root_shell "test -f '$DATA_DIR/config.ini' && grep -q '^\[AGENT\]' '$DATA_DIR/config.ini'"; then
    fatal 'agent registration did not complete successfully'
fi

ENV_TMP="$TMP_DIR/drastic-agent.env"
cat > "$ENV_TMP" <<EOF
DRASTIC_ENV=prod
DRASTIC_SERVER=$SERVER_URL
DRASTIC_AGENT_DATA_DIR=$DATA_DIR
DRASTIC_LOGLEVEL=INFO
EOF
run_root install -m 0644 "$ENV_TMP" "$ENV_FILE"

SERVICE_TMP="$TMP_DIR/drastic-agent.service"
cat > "$SERVICE_TMP" <<EOF
[Unit]
Description=dRastic Backup Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
ExecStart=$REAL_BINARY
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
EOF
run_root install -m 0644 "$SERVICE_TMP" "$SERVICE_FILE"
run_root systemctl daemon-reload
run_root systemctl enable "$SERVICE_NAME" >/dev/null

if [ "$NO_START" = true ]; then
    info "Installed $SERVICE_NAME. Start it with: sudo systemctl start $SERVICE_NAME"
else
    run_root systemctl restart "$SERVICE_NAME"
    info "Installed and started $SERVICE_NAME."
fi

info 'Uninstall with: sudo drastic-agent uninstall'
'''
    return textwrap.dedent(script).replace("__SERVER_URL__", quoted_server_url)
