# Project Structure

dRastic Backup is a monorepo with separate backend, frontend, agent, and shared library code.

```text
drastic-backup/
├── apps/
│   ├── backend/    # Python / Flask API
│   ├── frontend/   # Vue / Quasar web UI
│   └── agent/      # dRastic agent runtime
├── docs/           # User, deployment, and development documentation
├── libs/
│   └── python/common
├── scripts/
├── docker-compose.agent.dev.yaml
├── docker-compose.agent.yaml
├── docker-compose.dev.yaml
└── docker-compose.yaml
```

## Backend

`apps/backend` contains the Flask application, API routes, database models, migrations, seed commands, and server runtime scripts.

Important paths:

- `apps/backend/src/drastic_server` -- Backend package.
- `apps/backend/tests` -- Backend test suite.
- `apps/backend/scripts/run.sh` -- Container startup script.
- `apps/backend/storage` -- Local backend storage path.
- `apps/backend/import` -- Local import path.
- `apps/backend/data` -- Local backend runtime data.

## Frontend

`apps/frontend` contains the Quasar application.

Important paths:

- `apps/frontend/src/layouts` -- Application layouts and top-level navigation.
- `apps/frontend/src/pages` -- Route pages.
- `apps/frontend/src/components` -- Reusable UI components.
- `apps/frontend/src/stores` -- Pinia stores.
- `apps/frontend/quasar.config.js` -- Quasar and development proxy configuration.

## Agent

`apps/agent` contains the runtime that runs on backup hosts.

Important paths:

- `apps/agent/src/drastic_agent/jobs` -- Backup job handlers.
- `apps/agent/src/drastic_agent/agent` -- Agent state, commands, actions, and reporting.
- `apps/agent/src/drastic_agent/resticapi` -- Restic integration helpers.
- `apps/agent/src/drastic_agent/proxmox.py` -- Proxmox guest discovery and backup streaming.
- `apps/agent/data` -- Local development agent state.

## Shared Library

`libs/python/common` contains shared Python code used by backend and agent.

## Compose Files

- `docker-compose.dev.yaml` -- Main development stack.
- `docker-compose.agent.dev.yaml` -- Development agent stack.
- `docker-compose.yaml` -- Server stack for test and production.
- `docker-compose.agent.yaml` -- Agent stack for test and production.

## Environment Files

- `.env.default` -- Shared defaults.
- `.env.dev` -- Tracked local development defaults.
- `.env.dev.override` -- Optional local overrides, not committed.
- `.env.test.example` -- Test environment template.
- `.env.prod.example` -- Production environment template.

## Generated Output

- `docs-site/` -- Local MkDocs build output.
- `site/` -- Default MkDocs output path when no site dir is passed.
- `apps/frontend/dist/` -- Frontend build output.
- `apps/frontend/.quasar/` -- Quasar generated development files.
