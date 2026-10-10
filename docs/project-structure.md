# Project Structure

dRastic Backup is a monorepo with separate backend, frontend, agent, and shared library code.

See [Architecture](architecture.md) for module responsibilities, dependency direction and transaction/recovery rules.

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
- `apps/backend/src/drastic_server/views/api` -- HTTP contracts and application-error mapping.
- `apps/backend/src/drastic_server/services` -- Business use cases, grouped by responsibility.
- `apps/backend/src/drastic_server/services/jobs` -- Job configuration, queries, management and execution.
- `apps/backend/src/drastic_server/services/operations` -- Durable operation lifecycle, history and report ingestion.
- `apps/backend/src/drastic_server/services/agent/installer.py` -- Installer rendering, standalone controller delivery through `/install.py`, and reproducible backend build targets.
- `apps/backend/src/drastic_server/integrations` -- External transport adapters.
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
- `apps/frontend/src/api` -- HTTP endpoint calls without UI or store dependencies.
- `apps/frontend/src/composables` -- Reactive workflows such as job editing.
- `apps/frontend/quasar.config.js` -- Quasar and development proxy configuration.

## Agent

`apps/agent` contains the runtime that runs on backup hosts.

Important paths:

- `apps/agent/src/drastic_agent/jobs` -- Backup job handlers.
- `apps/agent/src/drastic_agent/runtime` -- Composition root, transport, command admission, scheduling, reporting, execution and updates.
- `apps/agent/src/drastic_agent/storage` -- SQLite tables, migrations, synced configuration and durable operation queue.
- `apps/agent/src/drastic_agent/agent` -- Operation/report state, action execution and protocol-facing schemas.
- `apps/agent/src/drastic_agent/services` -- Restore, retention, connection settings, repository access and credentials.
- `apps/agent/src/drastic_agent/integrations` -- Host-tool adapters, including verified restic installation.
- `scripts/drastic-agent-installer.py` -- Shared native/Docker lifecycle manager; installed independently as `/opt/drastic-agent/bin/installer.py` and stages backend-matched source, dependencies and restic before activation.
- `apps/agent/src/drastic_agent/proxmox.py` -- Proxmox guest discovery and backup streaming.
- `apps/agent/data` -- Local development agent state.

## Shared Library

`libs/python/common` contains shared Python code used by backend and agent.

`libs/python/common/src/drastic_common/restic` contains the shared restic client. Common also owns the shared agent protocol, scheduling rules, secret envelopes, process utilities and product version generation; it does not import application packages.

`drastic_common/backup_selection.py` translates literal File/TrueNAS path rules into Restic exclusions and identifies configurations requiring include-exception support. The frontend shares browser inheritance and state feedback in `src/utils/path-selection.js`.

`drastic_common/version.py` generates product versions and full source revisions. Packages and Docker builds preserve them in generated `build-version.txt` and `build-revision.txt` files, including when Git is unavailable at runtime.

## Compose Files

- `docker-compose.dev.yaml` -- Main development stack.
- `docker-compose.agent.dev.yaml` -- Development agent stack.
- `docker-compose.yaml` -- Server stack for test and production.
- `docker-compose.agent.yaml` -- Agent stack for test and production.

## Environment Files

- `.env.example` -- Server configuration template for Compose.
- `.env.agent.example` -- Standalone Docker agent configuration template.
- `.env` -- Active deployment configuration, not committed.
- `.env.dev` -- Tracked, ready-to-run local development configuration.
- `.env.dev.override` -- Optional local overrides, not committed.
- `.env.test.example` -- Test environment template.

## Generated Output

- `docs-site/` -- Local MkDocs build output.
- `site/` -- Default MkDocs output path when no site dir is passed.
- `apps/frontend/dist/` -- Frontend build output.
- `apps/frontend/.quasar/` -- Quasar generated development files.
