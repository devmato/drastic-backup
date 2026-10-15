# Development Setup

This page describes local development workflows. For production operation, use the [Deployment](deployment.md) guide.

Before changing application structure, read [Architecture](architecture.md) and [Project Structure](project-structure.md).

## Prerequisites

- Docker with Docker Compose v2.
- Python 3.11 or later.
- `uv` for Python environments.
- Node.js 22 for the frontend.
- Corepack/Yarn for frontend dependencies.
- `mkdocs-material` when previewing documentation locally without Docker.

## Development Stack

Start the default development stack from the repository root:

```bash
./scripts/dev.sh up
```

The default stack starts:

- `db` -- MariaDB.
- `backend` -- Flask API and built docs serving.
- `rest-server` -- Integrated restic REST endpoint for native repositories.
- `frontend` -- Quasar dev server.
- Docker agent -- Agent container for local end-to-end testing.

Open the frontend at the configured development frontend port. The default is `http://127.0.0.1:9050`.

Development credentials are seeded from `.env.dev` by default. The default local account is `admin` / `admin` unless changed in the environment file.

Create optional local overrides with:

```bash
./scripts/generate-env.sh dev
```

This writes `.env.dev.override`, which is loaded automatically by `./scripts/dev.sh` and ignored by Git.

## Run Without Docker Agent

Start the server and frontend stack without the Docker agent:

```bash
./scripts/dev.sh up --no-agent
```

Use this when the agent should run directly on the host or when testing Proxmox-related behavior.

## Run Agent Locally

Start an agent on the host against the Docker development backend:

```bash
./scripts/dev.sh agent-local
```

Run an agent with production-like environment files and an explicit state directory:

```bash
./scripts/dev.sh agent-local --env prod --data-dir /var/lib/drastic-agent
```

Override the server URL explicitly:

```bash
./scripts/dev.sh agent-local --env prod --server https://backup.example.net --data-dir /var/lib/drastic-agent
```

`agent-local` loads `.env.dev` and optional `.env.dev.override` for `dev`. For `test` and `prod` it loads `.env`. An explicit `--env-file` is loaded last.

## Test Native Installer From Git

For native installer testing, start the development stack without the Docker agent. A clean backend checkout provides its exact commit as the native installation target:

```bash
./scripts/dev.sh up --no-agent
```

```bash
curl -fsSL http://127.0.0.1:5050/install | bash -s -- \
  --user admin \
  --password admin
```

The target host needs `git`, `curl`, root or sudo access, and systemd (Linux) or launchd (macOS). Python and `uv` are installed privately under `/opt/drastic-agent`. The backend commit must be pushed to the configured agent repository. Dirty or unknown backend builds have no automatic installation target; for uncommitted changes use `bash scripts/install-drastic-agent.sh --source "$PWD" --server http://127.0.0.1:5050`. Manage the installed agent locally:

```bash
sudo drastic-agent update
```

```bash
sudo drastic-agent uninstall
```

If the installer runs from another host or VM, set `DRASTIC_PUBLIC_URL` to a URL reachable by that host before rendering `/install`.

`--ref <branch|tag|commit>` explicitly overrides one installation/update; subsequent updates use the backend again. Docker builds stamp both product version and full revision. When supplying `DRASTIC_VERSION` manually (for example from a linked Git worktree), also pass `DRASTIC_REVISION=<full-commit-sha>`.

### macOS Acceptance Check

Linux-side tests simulate launchctl for install/update/rollback/removal and exercise the unchanged shared file-backup/restore code. They cannot establish macOS TCC permission behavior or filesystem fidelity. Follow [macOS installation and permissions](agent-installation.md#macos-permissions-and-first-backup) on a real Mac:

1. Install from the same published ref as the bootstrap, grant access and create a file job using a small protected test folder. Include spaces/Unicode names, a symlink, an excluded file and a file with an extended attribute.
2. Run the backup **through the web UI**, restore to a separate physical directory and compare content, exclusions, modes, symlink targets and extended attributes.
3. Run another backup after changing a file; verify the matching parent and restore the new version. Exercise a scheduled run and retention with a disposable repository.
4. With permission removed, verify the report does not claim a complete successful backup. Restore permission, restart the daemon and repeat successfully.
5. Restart the Mac, unlock FileVault if enabled, check reconnection, then test waking from sleep.
6. Update via CLI and the web action against a macOS-capable backend; verify identity, configuration and permissions, then run another backup/restore. Test startup rollback with a deliberately failing test build. During older-backend development, use explicit CLI refs instead.
7. Uninstall without purge and reinstall; the identity must survive. Check purge separately using only disposable agent state.

Run the acceptance check on both Intel and Apple Silicon before claiming both are verified. macOS system logs and the agent/update logs named in the installation guide are the relevant evidence when a check fails.

## Reset Local State

Reset persistent development volumes without removing local images:

```bash
./scripts/dev.sh reset-storage
```

`reset-storage` and `clean` also remove local `apps/agent/data` runtime state so Docker and host-based agent runs reset consistently.

## Backend Checks

Run backend tests:

```bash
cd apps/backend
uv sync --frozen --dev
uv run pytest
```

The current Alembic history is a new `init` baseline for fresh databases. It intentionally does not provide an upgrade path from earlier development schemas because there were no production deployments to preserve when the baseline was replaced. Recreate an old development or test database instead of stamping or upgrading it to this baseline.

Verify the baseline against a disposable MariaDB container:

```bash
cd apps/backend
./scripts/test-migrations.sh
```

The script checks migration heads and history, upgrades a fresh database, runs Alembic's schema check, downgrades to `base`, and repeats the upgrade and check. Docker must be available.

Run lint checks for modified backend files:

```bash
cd apps/backend
uv run ruff check src/drastic_server/app.py
```

## Agent Checks

Run agent tests:

```bash
cd apps/agent
uv sync --frozen --dev
uv run pytest
```

## Common Library Checks

Run common library tests. Release workflow regression tests also require Bash, Git and `jq` (available on the GitHub Ubuntu runners); GitHub and Docker calls are simulated:

```bash
cd libs/python/common
uv sync --frozen --dev
uv run pytest
```

## Frontend Checks

Install and build the frontend:

```bash
cd apps/frontend
corepack enable
yarn install --immutable
yarn lint
yarn test
yarn build
```

## Documentation Checks

Build the documentation site from the repository root:

```bash
mkdocs build --site-dir docs-site
```

The development Compose stack builds the docs into `/tmp/drastic-docs-site` inside the backend container and serves them at `/docs/`.
