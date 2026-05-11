#!/bin/sh

set -e

echo 'Executing database migrations...'
uv run python -m flask --app run.py db upgrade

echo 'Executing bootstrap seed...'
uv run python -m flask --app run.py seed bootstrap

if [ "${DRASTIC_DEBUG:-0}" = "1" ]; then
    echo 'Starting app in debug mode'
    exec uv run python -u run.py --debug
else
    echo 'Starting app in production mode'
    exec uv run gunicorn -c gunicorn.conf.py run:app
fi
