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

For macOS protected folders, grant [Full Disk Access](agent-installation.md#macos-permissions-and-first-backup) to the actual agent Python and Restic executables, then restart the service. Terminal/sudo access alone does not authorize the background agent.

## Login or WebSocket Connection Fails

- With `DRASTIC_JWT_COOKIE_SECURE=true`, open the UI through **HTTPS**, not the direct HTTP port.
- Preserve `Host`, forwarded scheme and WebSocket upgrades through the [reverse proxy](deployment.md#reverse-proxy).
- Check the browser's network errors and backend logs. Do not fix a proxy problem by disabling secure cookies on a public deployment.

## Installer Rejects the Backend Build

- Managed installs need a clean backend version and full source revision, available from the configured Git repository.
- For custom Docker builds, pass both [version/revision arguments](ci-and-releases.md#local-docker-builds).
- For uncommitted local development, use installer `--source PATH`; do not invent a clean version for dirty code.

## Agent Rejects a Newer Local Database

Use a build compatible with the agent SQLite schema. A software downgrade does not downgrade state. Preserve the data directory and recover a matching state backup if needed; do not edit its schema-version marker or delete identity/key files to bypass the check.

## Operation Dispatch Is Unknown

- A timed-out start may already be executing. Inspect **Reports**, agent logs and connectivity before retrying; elapsed time alone does not prove failure.
- Existing reports reconcile by operation UUID after reconnection. Do not delete state or force-unlock an active repository to clear the display.

## Native Repository Access Fails

Native repositories are stored through the integrated `rest-server`. Verify that server and rest-server share the same storage mount:

```text
DRASTIC_REST_SERVER_STORAGE_PATH=/opt/drastic-server/restic
```

If generated repository URLs point to the wrong host, set `DRASTIC_PUBLIC_URL` to the canonical public backend URL.

Agents access native repositories through the backend's authenticated restic proxy, including for synchronized schedules. An offline agent cannot use a native repository if the backend or its proxy route is unavailable. This differs from a custom repository that the agent can reach directly with an already synchronized agent key.

## Retention Does Not Remove Backups

| Check | Action |
| --- | --- |
| No policy / Disabled | Review the job policy and account default. Deleted jobs use the default only if one exists. |
| Legacy or foreign owner tags | Automatic retention deliberately leaves these snapshots untouched. Review them in Restore; manual deletion is permanent. |
| No completion-confirmed run | All runs of that job are preserved. Complete a current backup before expecting automatic expiration. |
| Backend/context unavailable | Restore connectivity and synchronize. Missing context is not treated as an empty job list. |
| Failed integrity check | Investigate storage and run a full-data check; another passing sample cannot clear the block. |
| Pending finalization or cleanup | Resolve the log's underlying error, then start a backup in that repository or **Agent > Actions > Run retention**. There is no timed repository retry. |
| Prune failed after deletion | Deleted snapshots stay deleted; a later successful prune reclaims space. |

For a **confirmed stale lock**, use **Repositories > Unlock repository** with an assigned online agent. Unlock does not run cleanup or resume a failed chain. See [Finalization and Recovery](backup-and-restore.md#finalization-and-recovery).

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
