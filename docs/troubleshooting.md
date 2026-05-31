# Troubleshooting

## Backend Does Not Start

Check required production variables first:

- `DRASTIC_APP_MASTER_SECRET`
- `DRASTIC_SQLALCHEMY_DATABASE_URI`

Check container logs:

```bash
docker compose -f docker-compose.yaml --env-file .env.prod logs backend
```

## Database Connection Fails

Verify that MariaDB is healthy and that `DRASTIC_SQLALCHEMY_DATABASE_URI` points to the Compose service name `db` when running inside Docker.

For the default Compose stack, the database host should be `db`, not `127.0.0.1`.

## Agent Stays Offline

Check the agent logs:

```bash
docker compose -f docker-compose.agent.yaml --env-file .env.prod logs agent
```

Verify:

- `DRASTIC_SERVER` is reachable from the agent host.
- `DRASTIC_USER` and `DRASTIC_PASSWORD` are valid for initial registration.
- The agent data directory is persistent and writable.

## File Backup Cannot Find Paths

Docker agents see host files below `/mnt/host` by default.

If the host path is `/home/app/data`, configure the backup path as:

```text
/mnt/host/home/app/data
```

Also verify `DRASTIC_AGENT_HOST_ROOT_PATH` in the agent Compose configuration.

## Native Repository Access Fails

Native repositories are stored through the integrated `rest-server`. Verify that server and rest-server share the same storage mount:

```text
DRASTIC_REST_SERVER_STORAGE_PATH=/opt/drastic-server/restic
```

If generated repository URLs point to the wrong host, set `DRASTIC_PUBLIC_URL` to the canonical public backend URL.

## Repository Secret Operations Are Locked

Repository creation and new agent repository provisioning require the per-user recovery key returned during login. If the browser no longer has that key in memory, the UI asks for the account password in a confirmation dialog and retries the operation without navigating away. Changing a repository password after creation is not supported yet.

After an agent restart, repository jobs also need a successful backend sync so restic access keys can be hydrated into memory. If an agent-specific recovery envelope already exists, the agent can request it from the backend and decrypt it locally. If repository access still fails, verify the agent is online, assigned to the repository, and has synced after the assignment.

## API Documentation

The Swagger UI is available at:

```text
/api/docs
```

The user and deployment documentation is available at:

```text
/docs/
```
