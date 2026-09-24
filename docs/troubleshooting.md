# Troubleshooting

## Backend Does Not Start

Check required production variables first:

- `DRASTIC_APP_MASTER_SECRET`
- `DRASTIC_SQLALCHEMY_DATABASE_URI`

Check container logs:

```bash
docker compose -f docker-compose.yaml logs backend
```

## Database Connection Fails

Verify that MariaDB is healthy and that `DRASTIC_SQLALCHEMY_DATABASE_URI` points to the Compose service name `db` when running inside Docker.

For the default Compose stack, the database host should be `db`, not `127.0.0.1`.

## Agent Stays Offline

Check the agent logs:

```bash
docker compose -f docker-compose.agent.yaml logs agent
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

Agents access native repositories through the backend's authenticated restic proxy, including for synchronized schedules. An offline agent cannot use a native repository if the backend or its proxy route is unavailable. This differs from a custom repository that the agent can reach directly with an already synchronized agent key.

## Repository Secret Operations Are Locked

Repository creation and new agent repository provisioning require the per-user recovery key returned during login. If the browser no longer has that key in memory, the UI asks for the account password in a confirmation dialog and retries the operation without navigating away. Changing a repository password after creation is not supported yet.

After an agent restart, an already synchronized encrypted repository key can be decrypted with the persistent local agent key. If that key material is missing or invalid, the agent must reconnect so access can be synchronized or reprovisioned. Verify the agent is assigned to the repository and has synced after the assignment.

## Native Repository Deletion Was Interrupted

Native repository deletion first moves unlocked storage into a private quarantine and commits the database deletion before removing the quarantined files. If the database commit fails, the repository is restored immediately. Backend startup maintenance reconciles an interrupted deletion: it restores quarantined storage when the database row still exists and removes quarantine data when the row was committed as deleted.

Inspect backend startup logs for the reconciliation counts or run the maintenance command explicitly:

```bash
docker compose -f docker-compose.yaml exec backend \
  uv run flask --app run.py repository reconcile-quarantine
```

Locked repositories are not moved. Invalid or conflicting quarantine entries are logged and left untouched for manual investigation rather than deleted automatically.

## API Documentation

The Swagger UI is available at:

```text
/api/docs
```

The user and deployment documentation is available at:

```text
/docs/
```
