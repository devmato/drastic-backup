#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="docker-compose.dev.yaml"
AGENT_COMPOSE_FILE="docker-compose.agent.dev.yaml"
ENV_FILE=".env.dev"
OVERRIDE_ENV_FILE=".env.dev.override"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
AGENT_LOCAL_DATA_DIR="$PROJECT_ROOT/apps/agent/data"
LEGACY_AGENT_LOCAL_DATA_DIR="$PROJECT_ROOT/apps/agent/data-dev-native"
AGENT_LOCAL_WORKDIR="$PROJECT_ROOT/apps/agent"

export DRASTIC_DEV_UID="${DRASTIC_DEV_UID:-$(id -u)}"
export DRASTIC_DEV_GID="${DRASTIC_DEV_GID:-$(id -g)}"
export DRASTIC_DOCKER_GID="${DRASTIC_DOCKER_GID:-$(stat -c %g /var/run/docker.sock 2>/dev/null || id -g)}"

prepare_dev_mountpoints() {
    mkdir -p \
        "$PROJECT_ROOT/apps/backend/storage" \
        "$PROJECT_ROOT/apps/backend/storage/rest-server" \
        "$PROJECT_ROOT/apps/backend/import" \
        "$PROJECT_ROOT/apps/backend/data" \
        "$PROJECT_ROOT/apps/backend/data/asset-cache" \
        "$AGENT_LOCAL_DATA_DIR"
}

clear_backend_dev_data() {
    rm -rf \
        "$PROJECT_ROOT/apps/backend/storage/rest-server" \
        "$PROJECT_ROOT/apps/backend/data/asset-cache"

    prepare_dev_mountpoints
}

dc_base() {
    local args=(docker compose -f "$COMPOSE_FILE")
    if [[ -f "$OVERRIDE_ENV_FILE" ]]; then
        args+=(--env-file "$ENV_FILE" --env-file "$OVERRIDE_ENV_FILE")
    else
        args+=(--env-file "$ENV_FILE")
    fi
    args+=("$@")
    "${args[@]}"
}

dc_stack() {
    local args=(docker compose -f "$COMPOSE_FILE" -f "$AGENT_COMPOSE_FILE")
    if [[ -f "$OVERRIDE_ENV_FILE" ]]; then
        args+=(--env-file "$ENV_FILE" --env-file "$OVERRIDE_ENV_FILE")
    else
        args+=(--env-file "$ENV_FILE")
    fi
    args+=("$@")
    "${args[@]}"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") <command> [options]

Dev stack helper for drastic-backup.

Commands:
  up [opts]       Stack starten (standardmaessig inkl. Agent)
  agent-up [opts] Nur Agent im Docker-Stack starten
  agent-local     Agent direkt auf dem Host starten
  stop            Stack stoppen, Container behalten
  agent-stop      Docker-Agent stoppen
  down            Stack stoppen und Container entfernen
  reset-storage   Stack stoppen und Dev-Volumes entfernen
  clean           Stack stoppen + Volumes + lokale Images entfernen
  restart [opts]  Stack per down + up neu starten
  agent-restart   Agent per stop + up neu starten
  logs [service]  Logs folgen
  agent-logs      Agent-Logs folgen
  status          Service-Status anzeigen
  agent-status    Agent-Status anzeigen
  config          Aufgeloeste Compose-Konfiguration anzeigen
  agent-config    Compose-Konfiguration inkl. Agent anzeigen
  help            Diese Hilfe anzeigen

Optionen fuer up/restart:
  -d, --detach    Im Hintergrund starten
  --build         Images vor dem Start neu bauen
  --no-agent      Stack ohne Docker-Agent starten

Optionen fuer agent-local:
  --debug         Host-Agent explizit mit DEBUG starten
  --env <name>    Env-Dateien fuer dev/test/prod laden (Default: aktuelles DRASTIC_ENV oder dev)
  --env-file <p>  Zusaetzliche Env-Datei nach den Standarddateien laden
  --data-dir <p>  Datenverzeichnis fuer den lokalen Agent setzen
  --server <url>  DRASTIC_SERVER explizit setzen
EOF
}

invalid_option() {
    printf 'Unbekannte Option: %s\n\n' "$1" >&2
    usage >&2
    exit 1
}

parse_run_flags() {
    DETACH=0
    BUILD=0
    NO_AGENT=0

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--detach)
                DETACH=1
                ;;
            --build)
                BUILD=1
                ;;
            --no-agent)
                NO_AGENT=1
                ;;
            *)
                invalid_option "$1"
                ;;
        esac
        shift
    done
}

parse_agent_local_flags() {
    AGENT_LOCAL_DEBUG=0
    AGENT_LOCAL_ENV="${DRASTIC_ENV:-dev}"
    AGENT_LOCAL_ENV_FILE=""
    AGENT_LOCAL_DATA_DIR_OVERRIDE=""
    AGENT_LOCAL_SERVER_OVERRIDE=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --debug)
                AGENT_LOCAL_DEBUG=1
                ;;
            --env)
                shift
                [[ $# -gt 0 ]] || invalid_option "--env"
                AGENT_LOCAL_ENV="$1"
                ;;
            --env-file)
                shift
                [[ $# -gt 0 ]] || invalid_option "--env-file"
                AGENT_LOCAL_ENV_FILE="$1"
                ;;
            --data-dir)
                shift
                [[ $# -gt 0 ]] || invalid_option "--data-dir"
                AGENT_LOCAL_DATA_DIR_OVERRIDE="$1"
                ;;
            --server)
                shift
                [[ $# -gt 0 ]] || invalid_option "--server"
                AGENT_LOCAL_SERVER_OVERRIDE="$1"
                ;;
            *)
                invalid_option "$1"
                ;;
        esac
        shift
    done
}

load_env_file() {
    local env_path="$1"

    if [[ -f "$env_path" ]]; then
        set -a
        # shellcheck disable=SC1090
        . "$env_path"
        set +a
    fi
}

clear_agent_local_data() {
    rm -rf "$AGENT_LOCAL_DATA_DIR"
    rm -rf "$LEGACY_AGENT_LOCAL_DATA_DIR"
}

compose_up() {
    local compose_runner="dc_stack"
    local args=(up)

    prepare_dev_mountpoints

    if [[ "$NO_AGENT" -eq 1 ]]; then
        compose_runner="dc_base"
    fi

    if [[ "$DETACH" -eq 1 ]]; then
        args+=(-d)
    fi

    if [[ "$BUILD" -eq 1 ]]; then
        args+=(--build)
    fi

    "$compose_runner" "${args[@]}"
}

cmd_up() {
    parse_run_flags "$@"
    compose_up
}

cmd_stop() {
    dc_stack stop
}

cmd_agent_up() {
    parse_run_flags "$@"
    NO_AGENT=0
    local args=(up)

    prepare_dev_mountpoints

    if [[ "$DETACH" -eq 1 ]]; then
        args+=(-d)
    fi

    if [[ "$BUILD" -eq 1 ]]; then
        args+=(--build)
    fi

    args+=(agent)
    dc_stack "${args[@]}"
}

cmd_agent_stop() {
    dc_stack stop agent
}

cmd_down() {
    dc_stack down
}

cmd_reset_storage() {
    dc_stack down -v
    clear_backend_dev_data
    clear_agent_local_data
    prepare_dev_mountpoints
}

cmd_clean() {
    dc_stack down -v --rmi local
    clear_backend_dev_data
    clear_agent_local_data
    prepare_dev_mountpoints
}

cmd_restart() {
    parse_run_flags "$@"
    if [[ "$NO_AGENT" -eq 1 ]]; then
        dc_base down
    else
        dc_stack down
    fi
    compose_up
}

cmd_agent_restart() {
    parse_run_flags "$@"
    NO_AGENT=0
    dc_stack stop agent
    cmd_agent_up "$@"
}

cmd_logs() {
    if [[ -n "${1:-}" ]]; then
        dc_stack logs -f "$1"
    else
        dc_stack logs -f
    fi
}

cmd_agent_logs() {
    dc_stack logs -f agent
}

cmd_status() {
    dc_stack ps
}

cmd_agent_status() {
    dc_stack ps agent
}

cmd_config() {
    dc_base config
}

cmd_agent_config() {
    dc_stack config
}

cmd_agent_local() {
    parse_agent_local_flags "$@"

    local env_name="${AGENT_LOCAL_ENV:-dev}"
    local env_file="$PROJECT_ROOT/.env.${env_name}"
    local override_env_file="$PROJECT_ROOT/.env.${env_name}.override"

    load_env_file "$PROJECT_ROOT/.env.default"
    load_env_file "$env_file"
    load_env_file "$override_env_file"

    if [[ -n "$AGENT_LOCAL_ENV_FILE" ]]; then
        load_env_file "$AGENT_LOCAL_ENV_FILE"
    fi

    export DRASTIC_ENV="${AGENT_LOCAL_ENV:-${DRASTIC_ENV:-$env_name}}"
    export DRASTIC_AGENT_DATA_DIR="${AGENT_LOCAL_DATA_DIR_OVERRIDE:-${DRASTIC_AGENT_DATA_DIR:-$AGENT_LOCAL_DATA_DIR}}"
    export DRASTIC_SERVER="${AGENT_LOCAL_SERVER_OVERRIDE:-${DRASTIC_SERVER:-http://127.0.0.1:${DRASTIC_HOST_BACKEND_PORT:-5050}}}"
    export DRASTIC_USER="${DRASTIC_USER:-${DRASTIC_BOOTSTRAP_ADMIN_USERNAME:-${DRASTIC_DEV_SEED_ADMIN_USERNAME:-}}}"
    export DRASTIC_PASSWORD="${DRASTIC_PASSWORD:-${DRASTIC_BOOTSTRAP_ADMIN_PASSWORD:-${DRASTIC_DEV_SEED_ADMIN_PASSWORD:-}}}"

    if [[ "$AGENT_LOCAL_DEBUG" -eq 1 ]]; then
        export DRASTIC_LOGLEVEL="DEBUG"
    elif [[ "$DRASTIC_ENV" == "dev" || "$DRASTIC_ENV" == "test" ]]; then
        export DRASTIC_LOGLEVEL="${DRASTIC_LOGLEVEL:-DEBUG}"
    fi

    mkdir -p "$DRASTIC_AGENT_DATA_DIR"

    printf 'Starting local agent with env=%s data_dir=%s server=%s\n' \
        "$DRASTIC_ENV" "$DRASTIC_AGENT_DATA_DIR" "$DRASTIC_SERVER"

    cd "$AGENT_LOCAL_WORKDIR"
    uv run drastic-agent
}

cd "$PROJECT_ROOT"

case "${1:-}" in
    up)       cmd_up "${@:2}" ;;
    agent-up) cmd_agent_up "${@:2}" ;;
    agent-local) cmd_agent_local "${@:2}" ;;
    stop)     cmd_stop ;;
    agent-stop) cmd_agent_stop ;;
    down)     cmd_down ;;
    reset-storage) cmd_reset_storage ;;
    clean)    cmd_clean ;;
    restart)  cmd_restart "${@:2}" ;;
    agent-restart) cmd_agent_restart "${@:2}" ;;
    logs)     cmd_logs "${2:-}" ;;
    agent-logs) cmd_agent_logs ;;
    status)   cmd_status ;;
    agent-status) cmd_agent_status ;;
    config)   cmd_config ;;
    agent-config) cmd_agent_config ;;
    help|"") usage ;;
    *)
        printf 'Unbekannter Befehl: %s\n\n' "$1" >&2
        usage >&2
        exit 1
        ;;
esac
