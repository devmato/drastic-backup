# Architecture

dRastic is a service-oriented modular application in a monorepo. The backend coordinates configuration and operations, agents execute backups on their hosts, and the frontend presents the same workflows. Modules follow business responsibilities while retaining the Flask, SQLAlchemy, Vue/Quasar and restic stack.

## Application Boundaries

```text
Vue / Quasar frontend
        | HTTP + Socket.IO notifications
Flask backend ---- MariaDB
        | authenticated, versioned agent protocol
Agent runtime ---- local SQLite state / durable report queue
        | restic + host integrations
Backup repositories / Proxmox / TrueNAS
```

Backend and agent both depend on `libs/python/common/src/drastic_common`. The common library never imports an application. It holds shared protocol schemas, scheduling rules, secret envelopes, process execution and the restic client. It does not own backend database models or the agent lifecycle.

Services are Python functions or ordinary classes inside the existing applications, not separately deployed processes. A class is useful for related state/dependencies; a collection of stateless operations usually needs only functions.

## Backend

All paths below are relative to `apps/backend/src/drastic_server/`.

| Location | Responsibility |
| --- | --- |
| `app.py`, `extensions.py`, `config.py` | Application composition, extension registration and configuration |
| `views/api/` | HTTP schemas, authenticated identity, response serialization and status codes |
| `views/api/errors.py` | Application-error mapping and rollback of unfinished request work |
| `socketio/namespaces/` | Socket transport, session identifiers and response envelopes |
| `schemas/` | Input validation and serialization; shared wire contracts remain in Common |
| `models/` | SQLAlchemy mappings, constraints and model-local rules |
| `services/jobs/` | Job configuration, owner-scoped queries, management and execution |
| `services/agent/` | Agent registration, assignments, session lifecycle and synchronization requests |
| `services/operations/` | Durable start/failure transitions, report ingestion and history |
| `services/repository/` | Repository management, maintenance, credentials and native storage lifecycle |
| `services/chains.py` | Chain configuration, scheduling, leases and durable execution transitions |
| `services/retention.py`, `notifications.py`, `users.py`, `auth.py` | Policy, notification, account and session use cases |
| `integrations/agent.py` | Socket.IO command dispatch, protocol support and timeout classification |

### Dependency Direction

```text
HTTP / Socket.IO / CLI / background entry points
                        ↓
              application services
                        ↓
           models + integration adapters
```

Services may use SQLAlchemy directly and may need a Flask **application context** for the configured database, templates or settings. They must not require a Flask **request context**. Callers supply the user ID, input values and metadata such as user agent explicitly. Services enforce ownership themselves so an alternate caller cannot bypass authorization.

`services/queries.py::require_result` raises a `ResourceNotFound` application error rather than an HTTP exception. `ResourceConflict`, `AuthenticationFailed`, `InvalidInput` and `AgentCommandFailed` similarly cross the service boundary without choosing an HTTP response. The API translates them centrally. Existing restore-specific failures are translated by the restore boundary.

`services/jobs/configuration.py` validates and mutates objects without committing. Management services implement complete writes. Execution services coordinate committed operation state and remote commands. Keep these distinctions explicit when adding another operation.

### Transaction Ownership

The use case chooses commit points. Helper functions must not hide commits. Database work and network/filesystem operations cannot form one atomic transaction:

1. Validate ownership, references and prerequisites.
2. Persist the required intent/configuration and commit.
3. Publish the committed state or dispatch the external operation.
4. Reconcile acknowledgements and later reports through the existing operation lifecycle.

HTTP error handlers roll back any unfinished work. Background and CLI callers must likewise roll back on failure before reusing a session. Already committed intent remains durable and is reconciled, rather than being treated as if a remote side effect could be rolled back.

Native repository deletion follows its own compensating sequence: quarantine the directory, commit the database deletion, then purge the quarantine. A failed database transaction restores the directory; interrupted purges are reconciled by the existing maintenance logic.

## Agent

All paths below are relative to `apps/agent/src/drastic_agent/`.

| Location | Responsibility |
| --- | --- |
| `__main__.py` | CLI, signal handling and startup |
| `runtime/agent.py` | Compose process resources, configuration/identity and command entry points |
| `runtime/connection.py` | Socket callbacks and bounded request transport |
| `runtime/commands.py` | Command admission, UUID deduplication, dispatch and unexpected worker failures |
| `runtime/execution.py` | Bounded executor, atomic resource admission and maintenance exclusion |
| `runtime/scheduler.py` | Durable schedule claims, completion, interrupted-run recovery and pruning |
| `runtime/reporting.py` | Acknowledgement-aware report delivery and replay ordering |
| `runtime/updates.py` | Independent installer lifecycle, execution pause and update reports |
| `storage/database.py` | Local database tables and additive migrations |
| `storage/operation_store.py` | Durable operation/report queue |
| `storage/sync.py` | Atomic replacement of synced configuration |
| `agent/operation.py`, `agent/report.py` | Operation/report state, logs, progress and artifacts |
| `services/connections.py` | Persistent Proxmox/TrueNAS settings, validation and discovery |
| `services/repository_access.py`, `services/secrets.py` | Repository resolution, recovery-key provisioning and in-memory credentials |
| `services/restore.py`, `services/retention.py` | Restore and retention workflows |
| `jobs/` | Shared backup lifecycle and file, Proxmox and TrueNAS handlers |
| `integrations/restic_binary.py` | Verified restic installation and atomic binary replacement |
| `proxmox*.py`, `truenas.py`, guest-file modules | Specialized platform clients and host tooling |

The established public `drastic_agent.agent.Agent` entry point resolves to the runtime composition root. Internal imports use the actual module locations. Moving Python modules does not rename SQLite tables or change their serialized contents.

### Worker Dependencies

`BackupContext` and `RestoreContext` expose only capabilities needed by workers. A backup handler can access its repository client, hooks, retention and relevant platform clients. A restore worker needs the restic client and repository configuration. Neither context exposes the socket connection, scheduler or arbitrary runtime internals.

Runtime helpers receive focused dependencies: the scheduler gets tables and the executor submission function; reporting gets the request callback; secret processing gets keys, caches and the required persistence/transport operations. This permits meaningful tests without booting the entire agent or downloading tools.

### Durability And Concurrency

- Manual commands and schedules share the bounded executor and job/repository resource admission.
- Asynchronous commands reserve operation history before submission and deduplicate repeated UUIDs. Cancellation and admission retain their shared fence.
- Schedule slots are claimed persistently before execution. One-shot claims survive history cleanup, restarts and clock changes.
- Configuration sync replaces its tables in one local transaction, so an offline agent retains the last complete configuration on failure.
- Reports are snapshotted while locked, then sent without holding the lock during network I/O. Only the acknowledged log sequence advances; concurrent updates stay dirty.
- Child reports do not overtake their outstanding parents. Failed sends rotate so unrelated reports can progress.
- A request timeout does not tear down a connection that may already have reconnected.
- Agent updates reuse the independent installer. Admission stays paused while its outcome is uncertain.
- Failed integrity checks keep retention blocked until the required verification succeeds. Restore path, snapshot ownership and overwrite checks remain in the execution services.

## Frontend

Paths are relative to `apps/frontend/src/`.

```text
pages/ + components/     presentation, local form state, navigation, feedback
         ↓
composables/             multi-step reactive workflows
stores/                  shared state and refresh ownership
         ↓
api/                     endpoint calls and response payloads
         ↓
boot/axios.js            configured client, authentication and interceptors
```

Not every action needs every layer. A component may use an API function for a local read, or a store for shared data. API modules must not depend on stores, navigation, dialogs or notifications. The application boot boundary installs authentication handling; it is not a business workflow.

API modules group endpoints by jobs, agents, repositories, operations, restores, chains, policies, notifications and users. Existing stateless operation/restore stores delegate to these modules so their callers keep a single established entry point.

`composables/useJobEditor.js` owns job/action/schedule saving. It validates protocol restrictions before the first write, does not mutate the form draft and refreshes shared state once after a write sequence. The existing child endpoints commit independently: on partial failure the editor refreshes persisted state, retains the draft and reports the original error. It does not claim a transaction across multiple HTTP requests.

Continue using the shared backup-selection components and conventions described in the contributor guide. Architecture refactors do not require template, layout or styling changes.

## End-To-End Backup Flow

1. The frontend operation API submits a backup request.
2. The job route validates its schema and passes the authenticated user ID to `services/jobs/execution.py`.
3. The service checks ownership and prerequisites, synchronizes a newly assigned repository, and commits the operation through `services/operations/lifecycle.py`.
4. The agent transport sends the versioned command. A timeout leaves its dispatch outcome unknown.
5. The agent validates the command, reserves history and admits work through its executor.
6. The selected job handler executes with a `BackupContext` and records logs, progress and artifacts locally.
7. Reporting delivers snapshots and retries unacknowledged data. Backend report ingestion reconciles them with the committed operation.
8. Committed backend changes produce frontend notifications, which trigger the existing bounded reload behavior.

## Comments And Maintenance

Use short English module docstrings to explain responsibilities. Document important public contracts and non-obvious side effects. Inline comments should explain constraints such as commit-before-dispatch, lock scope, late acknowledgements, recovery ordering or protocol compatibility.

Prefer descriptive names and ordinary control flow. Avoid comment quotas, redundant narration, commented-out code and new abstractions without a concrete caller. Split by responsibility rather than line count; preserve meaningful existing tests when moving code.

See [Development Setup](development.md) for checks and [Project Structure](project-structure.md) for repository-level paths. Architecture changes should update this page, relevant tests and the agent guidelines in the same change.
