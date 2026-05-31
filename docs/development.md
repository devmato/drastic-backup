# Development Setup

This page describes local development workflows. For production operation, use the [Deployment](deployment.md) guide.

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

`agent-local` loads `.env.default`, `.env.<name>`, `.env.<name>.override`, and then an optional `--env-file`.

## Test Native Installer From Git

For installer and systemd testing without release artifacts, start the development stack without the Docker agent and install a native agent from a concrete git commit:

```bash
./scripts/dev.sh up --no-agent
```

```bash
curl -fsSL http://127.0.0.1:5050/install | bash -s -- \
  --action install \
  --source git \
  --version <commit-sha> \
  --user admin \
  --password admin \
  --no-start
```

The target host needs `git`, `uv`, Python, and systemd. The `/install` endpoint bootstraps `/opt/drastic-agent/agentctl`, which can later update or uninstall the local native agent without fetching the bootstrap script again:

```bash
sudo drastic-agent update --source git --version <commit-sha> --no-start
```

```bash
sudo drastic-agent uninstall
```

If the installer runs from another host or VM, set `DRASTIC_PUBLIC_URL` to a URL reachable by that host before rendering `/install`.

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

Run common library tests:

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
yarn build
```

## Documentation Checks

Build the documentation site from the repository root:

```bash
mkdocs build --site-dir docs-site
```

The development Compose stack builds the docs into `/tmp/drastic-docs-site` inside the backend container and serves them at `/docs/`.
