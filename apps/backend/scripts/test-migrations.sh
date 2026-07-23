#!/bin/sh

set -eu

backend_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
container_name="drastic-backup-migration-test-$$"
database_name=drastic_migration_test
database_user=drastic
database_password=drastic

cleanup() {
    docker rm --force "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run --detach \
    --name "$container_name" \
    --publish 127.0.0.1::3306 \
    --env MARIADB_ROOT_PASSWORD=root-password \
    --env MARIADB_DATABASE="$database_name" \
    --env MARIADB_USER="$database_user" \
    --env MARIADB_PASSWORD="$database_password" \
    mariadb:11.4 \
    --character-set-server=utf8mb4 \
    --collation-server=utf8mb4_unicode_ci >/dev/null

attempt=0
until docker exec "$container_name" mariadb-admin ping \
    --host=127.0.0.1 \
    --user="$database_user" \
    --password="$database_password" \
    --silent >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "MariaDB did not become ready" >&2
        exit 1
    fi
    sleep 1
done

database_port=$(docker port "$container_name" 3306/tcp)
database_port=${database_port##*:}
database_uri="mysql+pymysql://${database_user}:${database_password}@127.0.0.1:${database_port}/${database_name}?charset=utf8mb4"

run_flask() {
    DRASTIC_ENV=test \
        DRASTIC_APP_MASTER_SECRET=migration-test-secret \
        DRASTIC_SQLALCHEMY_DATABASE_URI="$database_uri" \
        uv run --directory "$backend_root" flask --app run.py "$@"
}

run_flask db heads
run_flask db history
run_flask db upgrade
run_flask db check
run_flask db downgrade base
run_flask db upgrade
run_flask db check
