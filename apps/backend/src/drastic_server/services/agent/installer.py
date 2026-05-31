from __future__ import annotations

import shlex
import textwrap


def render_linux_agent_install_script(server_url: str) -> str:
    quoted_server_url = shlex.quote(server_url.rstrip("/"))
    script = r'''#!/usr/bin/env bash
set -euo pipefail

SERVER_URL=__SERVER_URL__
AGENT_ROOT=/opt/drastic-agent
AGENTCTL="$AGENT_ROOT/agentctl"

info() {
    printf '[INFO] %s\n' "$*"
}

fatal() {
    printf '[ERROR] %s\n' "$*" >&2
    exit 1
}

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

ensure_root_runner() {
    if [ "$(id -u)" -eq 0 ]; then
        return
    fi
    command -v sudo >/dev/null 2>&1 || fatal 'this installer requires root or sudo'
}

download() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$2" "$1"
    else
        fatal 'curl or wget is required'
    fi
}

ensure_root_runner
TMP_DIR=$(mktemp -d -t drastic-agent-bootstrap.XXXXXXXXXX)
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

info "Installing local agent lifecycle manager to $AGENTCTL"
download "$SERVER_URL/agentctl" "$TMP_DIR/agentctl"
run_root mkdir -p "$AGENT_ROOT"
run_root install -m 0755 "$TMP_DIR/agentctl" "$AGENTCTL"

exec "$AGENTCTL" "$@"
'''
    return textwrap.dedent(script).replace("__SERVER_URL__", quoted_server_url)


def render_linux_agentctl_script(server_url: str, git_repository: str = "") -> str:
    quoted_server_url = shlex.quote(server_url.rstrip("/"))
    quoted_git_repository = shlex.quote(git_repository)
    script = r'''#!/usr/bin/env bash
set -euo pipefail

SERVER_URL=__SERVER_URL__
DEFAULT_GIT_REPOSITORY=__GIT_REPOSITORY__
AGENT_ROOT=/opt/drastic-agent
AGENTCTL="$AGENT_ROOT/agentctl"
REAL_BINARY="$AGENT_ROOT/bin/drastic-agent"
SOURCE_DIR="$AGENT_ROOT/source"
DATA_DIR="$AGENT_ROOT/data"
ENV_FILE="$AGENT_ROOT/drastic-agent.env"
WRAPPER=/usr/local/bin/drastic-agent
SERVICE_NAME=drastic-agent
SERVICE_FILE=/etc/systemd/system/drastic-agent.service

ACTION=${DRASTIC_AGENT_ACTION:-auto}
DRASTIC_USER_VALUE=${DRASTIC_USER:-}
DRASTIC_PASSWORD_VALUE=${DRASTIC_PASSWORD:-}
INSTALL_SOURCE=${DRASTIC_AGENT_INSTALL_SOURCE:-release}
INSTALL_VERSION=${DRASTIC_AGENT_INSTALL_VERSION:-latest}
GIT_REPOSITORY=${DRASTIC_AGENT_GIT_REPOSITORY:-$DEFAULT_GIT_REPOSITORY}
RESOLVED_INSTALL_REF=""
NO_START=false
PURGE=false
YES=false

usage() {
    cat <<'EOF'
dRastic Agent Lifecycle Manager

Usage:
  curl -fsSL https://drastic-url/install | bash
  curl -fsSL https://drastic-url/install | bash -s -- --user USER --password PASSWORD
  sudo drastic-agent update --source release --version latest
  sudo drastic-agent uninstall

Options:
  --action ACTION      Action: auto, install, update, or uninstall (default: auto)
  --install            Alias for --action install
  --update             Alias for --action update
  --uninstall          Alias for --action uninstall
  --user USER          dRastic username for initial agent registration
  --password PASSWORD  dRastic password for initial agent registration
  --source SOURCE      Install source: release or git (default: release)
  --version VERSION    Release tag or git ref to install (default: latest)
  --git-repo URL       Git repository used with --source git
  --no-start           Install and enable the service without starting it
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
        --action)
            [ -n "${2:-}" ] || fatal '--action requires a value'
            ACTION=$2
            shift 2
            ;;
        --install)
            ACTION=install
            shift
            ;;
        --update)
            ACTION=update
            shift
            ;;
        --uninstall)
            ACTION=uninstall
            shift
            ;;
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
        --source)
            [ -n "${2:-}" ] || fatal '--source requires a value'
            INSTALL_SOURCE=$2
            shift 2
            ;;
        --version)
            [ -n "${2:-}" ] || fatal '--version requires a value'
            INSTALL_VERSION=$2
            shift 2
            ;;
        --git-repo)
            [ -n "${2:-}" ] || fatal '--git-repo requires a value'
            GIT_REPOSITORY=$2
            shift 2
            ;;
        --no-start)
            NO_START=true
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

case "$ACTION" in
    auto|install|update|uninstall) ;;
    *) fatal "unsupported action: $ACTION" ;;
esac

case "$INSTALL_SOURCE" in
    release|git) ;;
    *) fatal "unsupported install source: $INSTALL_SOURCE" ;;
esac

[ -n "$INSTALL_VERSION" ] || fatal '--version cannot be empty'

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
    command -v sudo >/dev/null 2>&1 || fatal 'this command requires root or sudo'
}

download_url() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$2" "$1"
    else
        return 127
    fi
}

agent_installed() {
    [ -e "$WRAPPER" ] || [ -e "$REAL_BINARY" ] || [ -e "$SERVICE_FILE" ] || [ -d "$SOURCE_DIR" ]
}

agent_configured() {
    run_root_shell "test -f '$DATA_DIR/config.ini' && grep -q '^\[AGENT\]' '$DATA_DIR/config.ini'" >/dev/null 2>&1
}

resolve_action() {
    if [ "$ACTION" != auto ]; then
        return
    fi

    if ! agent_installed; then
        ACTION=install
        return
    fi

    if [ ! -r /dev/tty ]; then
        fatal 'agent is already installed; re-run with --action update or --action uninstall'
    fi

    printf 'dRastic agent is already installed. Choose action:\n' >/dev/tty
    printf '  1) Update agent\n' >/dev/tty
    printf '  2) Uninstall agent\n' >/dev/tty
    printf '  3) Cancel\n' >/dev/tty
    printf 'Selection [1-3]: ' >/dev/tty
    IFS= read -r choice </dev/tty
    case "$choice" in
        1) ACTION=update ;;
        2) ACTION=uninstall ;;
        3|"") exit 0 ;;
        *) fatal 'invalid selection' ;;
    esac
}

prompt_for_credentials_if_needed() {
    if [ "$ACTION" != install ] || agent_configured; then
        return
    fi

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
}

refresh_agentctl() {
    local agentctl_tmp="$TMP_DIR/agentctl"
    if download_url "$SERVER_URL/agentctl" "$agentctl_tmp"; then
        run_root install -m 0755 "$agentctl_tmp" "$AGENTCTL"
    else
        warn "Could not refresh $AGENTCTL from $SERVER_URL/agentctl"
    fi
}

install_from_release() {
    need_cmd tar
    local artifact_url="$SERVER_URL/agents/linux/$ARCH"
    local archive="$TMP_DIR/drastic-agent.tar.gz"

    RESOLVED_INSTALL_REF="$INSTALL_VERSION"
    if [ "$INSTALL_VERSION" != latest ]; then
        artifact_url="$artifact_url?version=$INSTALL_VERSION"
    fi

    info "Downloading agent artifact for linux/$ARCH from $artifact_url"
    download_url "$artifact_url" "$archive" || fatal 'curl or wget is required for release installs'
    tar -xzf "$archive" -C "$TMP_DIR"

    local extracted_binary
    extracted_binary=$(find "$TMP_DIR" -type f -name drastic-agent | head -n 1)
    [ -n "$extracted_binary" ] || fatal 'downloaded artifact did not contain drastic-agent'
    chmod 755 "$extracted_binary"

    info "Installing agent binary to $REAL_BINARY"
    run_root mkdir -p "$AGENT_ROOT/bin" "$DATA_DIR"
    run_root chmod 700 "$DATA_DIR"
    run_root rm -rf "$SOURCE_DIR"
    run_root install -m 0755 "$extracted_binary" "$REAL_BINARY"
}

install_from_git() {
    [ -n "$GIT_REPOSITORY" ] || fatal '--git-repo is required when --source git is used'
    need_cmd git
    need_cmd uv

    local uv_bin
    uv_bin=$(command -v uv)
    local checkout_dir="$TMP_DIR/source"
    local runner_tmp="$TMP_DIR/drastic-agent-git-runner"

    if [ "$INSTALL_VERSION" = latest ]; then
        warn 'Installing from git default branch because --version latest was requested. Use a commit SHA for reproducible tests.'
    fi

    info "Cloning agent source from $GIT_REPOSITORY"
    git clone "$GIT_REPOSITORY" "$checkout_dir" >/dev/null
    if [ "$INSTALL_VERSION" != latest ]; then
        git -C "$checkout_dir" checkout --detach "$INSTALL_VERSION" >/dev/null
    fi
    RESOLVED_INSTALL_REF=$(git -C "$checkout_dir" rev-parse --verify HEAD)

    [ -d "$checkout_dir/apps/agent" ] || fatal 'git repository does not contain apps/agent'

    info "Installing agent source to $SOURCE_DIR at $RESOLVED_INSTALL_REF"
    run_root mkdir -p "$AGENT_ROOT/bin" "$DATA_DIR"
    run_root chmod 700 "$DATA_DIR"
    run_root rm -rf "$SOURCE_DIR"
    run_root cp -a "$checkout_dir" "$SOURCE_DIR"

    info 'Installing git agent dependencies'
    run_root sh -c "cd '$SOURCE_DIR/apps/agent' && '$uv_bin' sync --frozen"

    {
        printf '#!/usr/bin/env bash\n'
        printf 'set -euo pipefail\n'
        printf 'export DRASTIC_AGENT_INSTALL_SOURCE=%q\n' git
        printf 'export DRASTIC_AGENT_INSTALL_VERSION=%q\n' "$INSTALL_VERSION"
        printf 'export DRASTIC_AGENT_INSTALL_REF=%q\n' "$RESOLVED_INSTALL_REF"
        printf 'cd %q\n' "$SOURCE_DIR/apps/agent"
        printf 'exec %q run drastic-agent "$@"\n' "$uv_bin"
    } > "$runner_tmp"
    run_root install -m 0755 "$runner_tmp" "$REAL_BINARY"
}

write_wrapper() {
    local wrapper_tmp="$TMP_DIR/drastic-agent-wrapper"
    cat > "$wrapper_tmp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

AGENT_ROOT=/opt/drastic-agent
AGENTCTL="$AGENT_ROOT/agentctl"
REAL_BINARY="$AGENT_ROOT/bin/drastic-agent"

case "${1:-}" in
    install)
        shift
        exec "$AGENTCTL" --action install "$@"
        ;;
    update)
        shift
        exec "$AGENTCTL" --action update "$@"
        ;;
    uninstall)
        shift
        exec "$AGENTCTL" --action uninstall "$@"
        ;;
esac

exec "$REAL_BINARY" "$@"
EOF
    run_root install -m 0755 "$wrapper_tmp" "$WRAPPER"
}

register_agent_if_needed() {
    if [ "$ACTION" != install ] || agent_configured; then
        return
    fi

    info "Registering agent with $SERVER_URL"
    if [ "$(id -u)" -eq 0 ]; then
        DRASTIC_ENV=prod \
        DRASTIC_SERVER="$SERVER_URL" \
        DRASTIC_USER="$DRASTIC_USER_VALUE" \
        DRASTIC_PASSWORD="$DRASTIC_PASSWORD_VALUE" \
        DRASTIC_AGENT_DATA_DIR="$DATA_DIR" \
        DRASTIC_AGENT_INSTALL_SOURCE="$INSTALL_SOURCE" \
        DRASTIC_AGENT_INSTALL_VERSION="$INSTALL_VERSION" \
        DRASTIC_AGENT_INSTALL_REF="$RESOLVED_INSTALL_REF" \
        "$REAL_BINARY" register
    else
        sudo env \
            DRASTIC_ENV=prod \
            "DRASTIC_SERVER=$SERVER_URL" \
            "DRASTIC_USER=$DRASTIC_USER_VALUE" \
            "DRASTIC_PASSWORD=$DRASTIC_PASSWORD_VALUE" \
            "DRASTIC_AGENT_DATA_DIR=$DATA_DIR" \
            "DRASTIC_AGENT_INSTALL_SOURCE=$INSTALL_SOURCE" \
            "DRASTIC_AGENT_INSTALL_VERSION=$INSTALL_VERSION" \
            "DRASTIC_AGENT_INSTALL_REF=$RESOLVED_INSTALL_REF" \
            "$REAL_BINARY" register
    fi

    if ! agent_configured; then
        fatal 'agent registration did not complete successfully'
    fi
    run_root chmod 600 "$DATA_DIR/config.ini"
}

write_runtime_files() {
    local env_tmp="$TMP_DIR/drastic-agent.env"
    cat > "$env_tmp" <<EOF
DRASTIC_ENV=prod
DRASTIC_SERVER=$SERVER_URL
DRASTIC_AGENT_DATA_DIR=$DATA_DIR
DRASTIC_LOGLEVEL=INFO
DRASTIC_AGENT_INSTALL_SOURCE=$INSTALL_SOURCE
DRASTIC_AGENT_INSTALL_VERSION=$INSTALL_VERSION
DRASTIC_AGENT_INSTALL_REF=$RESOLVED_INSTALL_REF
EOF
    run_root install -m 0644 "$env_tmp" "$ENV_FILE"

    local service_tmp="$TMP_DIR/drastic-agent.service"
    cat > "$service_tmp" <<EOF
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
    run_root install -m 0644 "$service_tmp" "$SERVICE_FILE"
    run_root systemctl daemon-reload
    run_root systemctl enable "$SERVICE_NAME" >/dev/null
}

start_or_restart_service() {
    if [ "$NO_START" = true ]; then
        info "Installed $SERVICE_NAME. Start it with: sudo systemctl start $SERVICE_NAME"
    else
        run_root systemctl restart "$SERVICE_NAME"
        info "Installed and started $SERVICE_NAME."
    fi
}

run_install_or_update() {
    if [ "$ACTION" = install ] && agent_installed; then
        fatal 'agent is already installed; use --action update or --action uninstall'
    fi
    if [ "$ACTION" = update ] && ! agent_installed; then
        fatal 'agent is not installed; use --action install'
    fi
    if [ "$ACTION" = update ] && ! agent_configured; then
        fatal 'agent is installed but not configured; refusing update without agent config'
    fi

    case "$INSTALL_SOURCE" in
        release) install_from_release ;;
        git) install_from_git ;;
    esac

    refresh_agentctl
    write_wrapper
    register_agent_if_needed
    write_runtime_files
    start_or_restart_service
    info 'Uninstall with: sudo drastic-agent uninstall'
}

confirm_purge() {
    if [ "$PURGE" != true ] || [ "$YES" = true ]; then
        return
    fi
    [ -r /dev/tty ] || fatal 'refusing to purge without a TTY. Re-run with --yes to confirm.'
    printf 'Remove all local dRastic agent data in %s? [y/N] ' "$DATA_DIR" >/dev/tty
    IFS= read -r answer </dev/tty
    case "$answer" in
        y|Y|yes|YES) ;;
        *) info 'Purge aborted.'; exit 0 ;;
    esac
}

run_uninstall() {
    if ! agent_installed; then
        fatal 'agent is not installed'
    fi

    confirm_purge

    if command -v systemctl >/dev/null 2>&1; then
        run_root systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
        run_root systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi

    run_root rm -f "$SERVICE_FILE" "$ENV_FILE" "$REAL_BINARY" "$WRAPPER" "$AGENTCTL"
    run_root rm -rf "$SOURCE_DIR"

    if [ "$PURGE" = true ]; then
        run_root rm -rf "$DATA_DIR"
    fi

    run_root rmdir "$AGENT_ROOT/bin" >/dev/null 2>&1 || true
    run_root rmdir "$AGENT_ROOT" >/dev/null 2>&1 || true

    if command -v systemctl >/dev/null 2>&1; then
        run_root systemctl daemon-reload >/dev/null 2>&1 || true
        run_root systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi

    info 'dRastic agent uninstalled.'
    if [ "$PURGE" != true ]; then
        info "Kept agent data in $DATA_DIR. Re-run with --purge to remove it."
    fi
}

resolve_action
ensure_root_runner
[ "$(uname -s)" = Linux ] || fatal 'this command currently supports Linux only'

if [ "$ACTION" != uninstall ]; then
    need_cmd systemctl
fi
need_cmd mktemp
need_cmd chmod
need_cmd mkdir

case "$(uname -m)" in
    x86_64|amd64) ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) fatal "unsupported CPU architecture: $(uname -m)" ;;
esac

TMP_DIR=$(mktemp -d -t drastic-agent-lifecycle.XXXXXXXXXX)
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

case "$ACTION" in
    install|update)
        prompt_for_credentials_if_needed
        run_install_or_update
        ;;
    uninstall)
        run_uninstall
        ;;
esac
'''
    return (
        textwrap.dedent(script)
        .replace("__SERVER_URL__", quoted_server_url)
        .replace("__GIT_REPOSITORY__", quoted_git_repository)
    )
