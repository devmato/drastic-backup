#!/bin/sh

set -e

DRASTIC_AGENT_DATA_DIR=${DRASTIC_AGENT_DATA_DIR:-/app/data}
export DRASTIC_AGENT_DATA_DIR

SSH_DIR="$DRASTIC_AGENT_DATA_DIR/ssh"
SSH_KEYFILE="$SSH_DIR/id_rsa"
SSH_HOME_DIR="${HOME:-/tmp}/.ssh"

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"
mkdir -p "$(dirname "$SSH_HOME_DIR")"

if [ -e "$SSH_HOME_DIR" ] && [ ! -L "$SSH_HOME_DIR" ]; then
    rm -rf "$SSH_HOME_DIR"
fi

if [ ! -L "$SSH_HOME_DIR" ]; then
    ln -s "$SSH_DIR" "$SSH_HOME_DIR"
fi

if ! test -f "$SSH_KEYFILE"; then
    echo 'Generating SSH-keypair...'
    ssh-keygen -q -t rsa -b 4096 -N '' -f "$SSH_KEYFILE"
    chmod 600 "$SSH_KEYFILE"
    chmod 644 "$SSH_KEYFILE.pub"
    echo 'done'
fi

if [ "${DRASTIC_ENV:-prod}" = "dev" ] || [ "${DRASTIC_ENV:-prod}" = "test" ]; then
    echo 'Starting app in debug mode'
else
    echo 'Starting app in production mode'
fi

if [ "${DRASTIC_AGENT_HOT_RELOAD:-0}" = "1" ] && { [ "${DRASTIC_ENV:-prod}" = "dev" ] || [ "${DRASTIC_ENV:-prod}" = "test" ]; }; then
    echo 'Starting agent with hot reload for src/drastic_agent'
    exec uv run python -m drastic_agent.dev
fi

exec uv run drastic-agent
