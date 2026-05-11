#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TARGET_ENV_DEFAULT="dev"
TARGET_ENV_INPUT="${1:-$TARGET_ENV_DEFAULT}"
if [ "$#" -gt 1 ]; then
    echo "Usage: $0 [dev|test|prod]" >&2
    exit 1
fi
TARGET_ENV_INPUT="$(printf '%s' "$TARGET_ENV_INPUT" | tr '[:upper:]' '[:lower:]')"
case "$TARGET_ENV_INPUT" in
    dev|test|prod) ;;
    *) TARGET_ENV_INPUT="$TARGET_ENV_DEFAULT" ;;
esac

TARGET_ENV=""
TARGET_FILE_NAME=""
TARGET_FILE=""
ENV_LABEL=""
FILE_CONTEXT_LINE_1=""
FILE_CONTEXT_LINE_2=""
FINAL_HINT_LINE_1=""
FINAL_HINT_LINE_2=""

prompt() {
    local question="$1"
    local default="${2:-}"
    if [ -n "$default" ]; then
        printf "%s [%s]: " "$question" "$default" >&2
    else
        printf "%s: " "$question" >&2
    fi
    read -r REPLY
    REPLY="${REPLY:-$default}"
}

confirm() {
    local question="$1"
    local default="${2:-n}"
    if [ "$default" = "y" ]; then
        printf "%s [Y/n]: " "$question" >&2
    else
        printf "%s [y/N]: " "$question" >&2
    fi
    read -r REPLY
    REPLY="${REPLY:-$default}"
    case "$REPLY" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

hr() {
    printf '\n%s\n' "----------------------------------------------------"
}

json_escape() {
    local s="$1"
    s="${s//\/\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

quote_env_string() {
    printf '"%s"' "$(json_escape "$1")"
}

build_setting_line() {
    local key="$1"
    local value="$2"
    if [ -z "$value" ]; then
        return
    fi
    printf '%s=%s\n' "$key" "$(quote_env_string "$value")"
}

derive_agent_image() {
    local server_image="$1"
    local image_without_tag="$server_image"
    local image_tag="latest"

    if [[ "$server_image" == *:* && "$server_image" != *://* ]]; then
        image_without_tag="${server_image%:*}"
        image_tag="${server_image##*:}"
    fi

    if [[ "$image_without_tag" == */drastic-backup-server ]]; then
        printf '%s:%s' "${image_without_tag%/drastic-backup-server}/drastic-backup-agent" "$image_tag"
    else
        printf '%s-agent:%s' "$image_without_tag" "$image_tag"
    fi
}

repository_version_tag() {
    local version_file="$REPO_ROOT/VERSION"
    if [ -f "$version_file" ]; then
        printf 'v%s' "$(tr -d '[:space:]' < "$version_file")"
    else
        printf 'v0.1.0'
    fi
}

generate_random_hex() {
    local bytes="${1:-32}"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$bytes" <<'PY'
import secrets
import sys

print(secrets.token_hex(int(sys.argv[1])))
PY
        return
    fi
    python - "$bytes" <<'PY'
import secrets
import sys

print(secrets.token_hex(int(sys.argv[1])))
PY
}

set_target_env() {
    TARGET_ENV="$1"
    case "$TARGET_ENV" in
        dev)
            TARGET_FILE_NAME=".env.dev.override"
            ENV_LABEL="development override"
            FILE_CONTEXT_LINE_1="# This file overrides defaults from .env.default and .env.dev."
            FILE_CONTEXT_LINE_2="# The dev compose stack also loads it automatically when present."
            FINAL_HINT_LINE_1="  Start the dev stack with:"
            FINAL_HINT_LINE_2="    ./scripts/dev.sh up"
            ;;
        test)
            TARGET_FILE_NAME=".env.test"
            ENV_LABEL="test"
            FILE_CONTEXT_LINE_1="# This file is a complete local test environment file."
            FILE_CONTEXT_LINE_2="# It is gitignored; keep .env.test.example as the tracked reference."
            FINAL_HINT_LINE_1="  Start the test server stack with:"
            FINAL_HINT_LINE_2="    docker compose -f docker-compose.yaml --env-file .env.test up -d"
            ;;
        prod)
            TARGET_FILE_NAME=".env.prod"
            ENV_LABEL="production"
            FILE_CONTEXT_LINE_1="# This file is a complete local production environment file."
            FILE_CONTEXT_LINE_2="# It is gitignored; keep .env.prod.example as the tracked reference."
            FINAL_HINT_LINE_1="  Start production with:"
            FINAL_HINT_LINE_2="    docker compose -f docker-compose.yaml --env-file .env.prod up -d"
            ;;
    esac
    TARGET_FILE="$REPO_ROOT/$TARGET_FILE_NAME"
}

prompt_dev_values() {
    hr
    echo "  Development override"
    echo ""
    prompt "    Host backend port (optional)" ""
    DEV_HOST_BACKEND_PORT="$REPLY"
    prompt "    Host frontend port (optional)" ""
    DEV_HOST_FRONTEND_PORT="$REPLY"
    prompt "    Public URL (optional)" ""
    DEV_PUBLIC_URL="$REPLY"
    prompt "    Proxmox API URL (optional)" ""
    DEV_PROXMOX_API_URL="$REPLY"
    prompt "    Proxmox token id (optional)" ""
    DEV_PROXMOX_TOKEN_ID="$REPLY"
    prompt "    Proxmox token secret (optional)" ""
    DEV_PROXMOX_TOKEN_SECRET="$REPLY"
}

write_dev_file() {
    DEV_HOST_BACKEND_PORT_BLOCK="$(build_setting_line "DRASTIC_HOST_BACKEND_PORT" "$DEV_HOST_BACKEND_PORT")"
    DEV_HOST_FRONTEND_PORT_BLOCK="$(build_setting_line "DRASTIC_HOST_DEV_FRONTEND_PORT" "$DEV_HOST_FRONTEND_PORT")"
    DEV_PUBLIC_URL_BLOCK="$(build_setting_line "DRASTIC_PUBLIC_URL" "$DEV_PUBLIC_URL")"
    DEV_PROXMOX_API_URL_BLOCK="$(build_setting_line "DRASTIC_PROXMOX_API_URL" "$DEV_PROXMOX_API_URL")"
    DEV_PROXMOX_TOKEN_ID_BLOCK="$(build_setting_line "DRASTIC_PROXMOX_TOKEN_ID" "$DEV_PROXMOX_TOKEN_ID")"
    DEV_PROXMOX_TOKEN_SECRET_BLOCK="$(build_setting_line "DRASTIC_PROXMOX_TOKEN_SECRET" "$DEV_PROXMOX_TOKEN_SECRET")"

    cat > "$TARGET_FILE" <<EOF
# dRastic Backup development override file
# Generated by scripts/generate-env.sh on $(date +%Y-%m-%d)
#
${FILE_CONTEXT_LINE_1}
${FILE_CONTEXT_LINE_2}
# It is gitignored and should never be committed.

# --- Common dev overrides (optional) ---
${DEV_HOST_BACKEND_PORT_BLOCK}${DEV_HOST_FRONTEND_PORT_BLOCK}${DEV_PUBLIC_URL_BLOCK}# DRASTIC_HOST_BACKEND_PORT=5051
# DRASTIC_HOST_DEV_FRONTEND_PORT=9051
# DRASTIC_PUBLIC_URL="http://127.0.0.1:5051"
# DRASTIC_SERVER="http://localhost:5050"
# DRASTIC_USER="admin"
# DRASTIC_PASSWORD="admin"

# --- Proxmox agent overrides (optional) ---
${DEV_PROXMOX_API_URL_BLOCK}${DEV_PROXMOX_TOKEN_ID_BLOCK}${DEV_PROXMOX_TOKEN_SECRET_BLOCK}# DRASTIC_PROXMOX_API_URL="https://127.0.0.1:8006/api2/json"
# DRASTIC_PROXMOX_TOKEN_ID="root@pam!drastic-agent"
# DRASTIC_PROXMOX_TOKEN_SECRET="change-me"
# DRASTIC_PROXMOX_VERIFY_TLS=false
EOF
}

prompt_server_agent_values() {
    local default_public_url="$1"
    local default_server_image="$2"
    local default_data_root="$3"

    ROOT_PASSWORD_DEFAULT="$(generate_random_hex 16)"
    DB_PASSWORD_DEFAULT="$(generate_random_hex 16)"
    SECRET_DEFAULT="$(generate_random_hex 32)"
    REPOSITORY_PASSWORD_DEFAULT="$(generate_random_hex 24)"

    prompt "    Server image" "$default_server_image"
    SERVER_IMAGE="$REPLY"
    prompt "    Agent image" "$(derive_agent_image "$SERVER_IMAGE")"
    AGENT_IMAGE="$REPLY"
    prompt "    Agent release base URL" "https://github.com"
    AGENT_RELEASE_BASE_URL="$REPLY"
    prompt "    Agent release repository" "devmato/drastic-backup"
    AGENT_RELEASE_REPOSITORY="$REPLY"
    prompt "    Agent artifact tag" "$(repository_version_tag)"
    AGENT_ARTIFACT_TAG="$REPLY"
    prompt "    Time zone" "UTC"
    ENV_TZ="$REPLY"
    prompt "    MariaDB root password" "$ROOT_PASSWORD_DEFAULT"
    MARIADB_ROOT_PASSWORD_VALUE="$REPLY"
    prompt "    MariaDB database" "drastic"
    MARIADB_DATABASE_VALUE="$REPLY"
    prompt "    MariaDB user" "drastic"
    MARIADB_USER_VALUE="$REPLY"
    prompt "    MariaDB password" "$DB_PASSWORD_DEFAULT"
    MARIADB_PASSWORD_VALUE="$REPLY"
    prompt "    App master secret" "$SECRET_DEFAULT"
    APP_MASTER_SECRET="$REPLY"
    prompt "    Public URL (optional, recommended for agent Compose)" "$default_public_url"
    PUBLIC_URL="$REPLY"
    prompt "    Backend bind port" "5050"
    BIND_BACKEND_PORT="$REPLY"
    prompt "    Host backend port" "$BIND_BACKEND_PORT"
    HOST_BACKEND_PORT="$REPLY"
    prompt "    Host database path" "$default_data_root/db"
    HOST_DB_PATH="$REPLY"
    prompt "    Host restic storage path" "$default_data_root/restic"
    REST_SERVER_STORAGE_PATH="$REPLY"
    prompt "    Asset cache path" "$default_data_root/asset-cache"
    ASSET_CACHE_PATH="$REPLY"
    prompt "    Agent data path" "/opt/drastic-agent/data"
    AGENT_HOST_DATA_PATH="$REPLY"
    prompt "    Agent host root path" "/"
    AGENT_HOST_ROOT_PATH="$REPLY"
    prompt "    Bootstrap admin username" "admin"
    BOOTSTRAP_ADMIN_USERNAME="$REPLY"
    prompt "    Bootstrap admin password" "$(generate_random_hex 12)"
    BOOTSTRAP_ADMIN_PASSWORD="$REPLY"
    prompt "    Bootstrap repository name" "Bootstrap Repository"
    BOOTSTRAP_REPOSITORY_NAME="$REPLY"
    prompt "    Bootstrap repository location" "s3:s3.example.net/drastic-bootstrap"
    BOOTSTRAP_REPOSITORY_LOCATION="$REPLY"
    prompt "    Bootstrap repository password" "$REPOSITORY_PASSWORD_DEFAULT"
    BOOTSTRAP_REPOSITORY_PASSWORD="$REPLY"
    prompt "    Proxmox API URL (optional)" ""
    PROXMOX_API_URL="$REPLY"
    prompt "    Proxmox token id (optional)" ""
    PROXMOX_TOKEN_ID="$REPLY"
    prompt "    Proxmox token secret (optional)" ""
    PROXMOX_TOKEN_SECRET="$REPLY"
}

write_server_agent_env_file() {
    local env_name="$1"
    local title="$2"
    local secure_cookies="$3"
    local testing="$4"
    local proxmox_api_block proxmox_token_id_block proxmox_token_secret_block

    proxmox_api_block="$(build_setting_line "DRASTIC_PROXMOX_API_URL" "$PROXMOX_API_URL")"
    proxmox_token_id_block="$(build_setting_line "DRASTIC_PROXMOX_TOKEN_ID" "$PROXMOX_TOKEN_ID")"
    proxmox_token_secret_block="$(build_setting_line "DRASTIC_PROXMOX_TOKEN_SECRET" "$PROXMOX_TOKEN_SECRET")"

    cat > "$TARGET_FILE" <<EOF
# dRastic Backup ${title} environment
# Generated by scripts/generate-env.sh on $(date +%Y-%m-%d)
#
${FILE_CONTEXT_LINE_1}
${FILE_CONTEXT_LINE_2}

DRASTIC_ENV=$env_name
TZ=$(quote_env_string "$ENV_TZ")

DRASTIC_SERVER_IMAGE=$(quote_env_string "$SERVER_IMAGE")
DRASTIC_AGENT_IMAGE=$(quote_env_string "$AGENT_IMAGE")
DRASTIC_AGENT_RELEASE_BASE_URL=$(quote_env_string "$AGENT_RELEASE_BASE_URL")
DRASTIC_AGENT_RELEASE_REPOSITORY=$(quote_env_string "$AGENT_RELEASE_REPOSITORY")
DRASTIC_AGENT_ARTIFACT_TAG=$(quote_env_string "$AGENT_ARTIFACT_TAG")
DRASTIC_AGENT_ARTIFACT_NAME_TEMPLATE=drastic-agent-{os}-{arch}-{tag}.{ext}
DRASTIC_ASSET_CACHE_PATH=$(quote_env_string "$ASSET_CACHE_PATH")

MARIADB_ROOT_PASSWORD=$(quote_env_string "$MARIADB_ROOT_PASSWORD_VALUE")
MARIADB_DATABASE=$(quote_env_string "$MARIADB_DATABASE_VALUE")
MARIADB_USER=$(quote_env_string "$MARIADB_USER_VALUE")
MARIADB_PASSWORD=$(quote_env_string "$MARIADB_PASSWORD_VALUE")

DRASTIC_SQLALCHEMY_DATABASE_URI=$(quote_env_string "mysql+pymysql://${MARIADB_USER_VALUE}:${MARIADB_PASSWORD_VALUE}@db:3306/${MARIADB_DATABASE_VALUE}?charset=utf8mb4")
DRASTIC_APP_MASTER_SECRET=$(quote_env_string "$APP_MASTER_SECRET")
DRASTIC_PUBLIC_URL=$(quote_env_string "$PUBLIC_URL")
DRASTIC_TESTING=$testing
DRASTIC_JWT_TOKEN_LOCATION=["cookies"]
DRASTIC_JWT_COOKIE_SECURE=$secure_cookies
DRASTIC_JWT_COOKIE_SAMESITE=Lax
DRASTIC_JWT_SESSION_COOKIE=false

DRASTIC_BIND_BACKEND_PORT=$BIND_BACKEND_PORT
DRASTIC_HOST_BACKEND_PORT=$HOST_BACKEND_PORT
DRASTIC_HOST_DB_PATH=$(quote_env_string "$HOST_DB_PATH")
DRASTIC_REST_SERVER_STORAGE_PATH=$(quote_env_string "$REST_SERVER_STORAGE_PATH")

DRASTIC_REST_PROXY_TIMEOUT_SECONDS=300
DRASTIC_COMMAND_TIMEOUT_SECONDS=60
DRASTIC_TASK_TIMEOUT_SECONDS=600

DRASTIC_BOOTSTRAP_ADMIN_USERNAME=$(quote_env_string "$BOOTSTRAP_ADMIN_USERNAME")
DRASTIC_BOOTSTRAP_ADMIN_PASSWORD=$(quote_env_string "$BOOTSTRAP_ADMIN_PASSWORD")
DRASTIC_BOOTSTRAP_ADMIN_UPDATE=false
DRASTIC_BOOTSTRAP_REPOSITORY_NAME=$(quote_env_string "$BOOTSTRAP_REPOSITORY_NAME")
DRASTIC_BOOTSTRAP_REPOSITORY_LOCATION=$(quote_env_string "$BOOTSTRAP_REPOSITORY_LOCATION")
DRASTIC_BOOTSTRAP_REPOSITORY_PASSWORD=$(quote_env_string "$BOOTSTRAP_REPOSITORY_PASSWORD")

DRASTIC_SERVER=$(quote_env_string "$PUBLIC_URL")
DRASTIC_AGENT_HOST_DATA_PATH=$(quote_env_string "$AGENT_HOST_DATA_PATH")
DRASTIC_AGENT_HOST_ROOT_PATH=$(quote_env_string "$AGENT_HOST_ROOT_PATH")
DRASTIC_LOGLEVEL=INFO
${proxmox_api_block}${proxmox_token_id_block}${proxmox_token_secret_block}DRASTIC_PROXMOX_VERIFY_TLS=false

# Agent registration still uses user/password. Repository traffic uses agent secrets.
DRASTIC_USER=$(quote_env_string "$BOOTSTRAP_ADMIN_USERNAME")
DRASTIC_PASSWORD=$(quote_env_string "$BOOTSTRAP_ADMIN_PASSWORD")
EOF
}

prompt_test_values() {
    hr
    echo "  Test environment"
    echo ""
    prompt_server_agent_values "https://backup-test.example.net" "ghcr.io/devmato/drastic-backup-server:$(repository_version_tag)" "/opt/drastic-test"
}

write_test_file() {
    write_server_agent_env_file "test" "test" "false" "true"
}

prompt_prod_values() {
    hr
    echo "  Production environment"
    echo ""
    prompt_server_agent_values "https://backup.example.net" "ghcr.io/devmato/drastic-backup-server:$(repository_version_tag)" "/opt/drastic-server"
}

write_prod_file() {
    write_server_agent_env_file "prod" "production" "false" "false"
}

echo ""
echo "dRastic Backup env generator"
echo "============================"
echo ""

while true; do
    prompt "  Which env file should be created? (dev/test/prod)" "$TARGET_ENV_INPUT"
    TARGET_ENV_INPUT="$(printf '%s' "$REPLY" | tr '[:upper:]' '[:lower:]')"
    case "$TARGET_ENV_INPUT" in
        dev|test|prod)
            set_target_env "$TARGET_ENV_INPUT"
            break
            ;;
        *)
            echo "  Invalid choice. Please enter dev, test, or prod."
            echo ""
            TARGET_ENV_INPUT="$TARGET_ENV_DEFAULT"
            ;;
    esac
done

echo "This script creates $TARGET_FILE_NAME with dRastic Backup ${ENV_LABEL} settings."
echo ""

if [ -f "$TARGET_FILE" ]; then
    if ! confirm "  $TARGET_FILE_NAME already exists. Overwrite?"; then
        echo "Aborted."
        exit 0
    fi
    echo ""
fi

case "$TARGET_ENV" in
    dev)
        prompt_dev_values
        hr
        echo "  Writing $TARGET_FILE_NAME ..."
        echo ""
        write_dev_file
        ;;
    test)
        prompt_test_values
        hr
        echo "  Writing $TARGET_FILE_NAME ..."
        echo ""
        write_test_file
        ;;
    prod)
        prompt_prod_values
        hr
        echo "  Writing $TARGET_FILE_NAME ..."
        echo ""
        write_prod_file
        ;;
esac

echo "  Done! Written to: $TARGET_FILE_NAME"
echo ""
echo "$FINAL_HINT_LINE_1"
echo "$FINAL_HINT_LINE_2"
echo ""
