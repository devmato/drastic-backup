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
| `runtime/scheduler.py` | Durable schedule claims, bounded retries, cancellation, interrupted-run recovery and pruning |
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
| `integrations/restic_binary.py` | Local restic version checks and atomic binary replacement, using Common's verified release downloader |
| `proxmox*.py`, `truenas.py`, guest-file modules | Specialized platform clients and host tooling |

The established public `drastic_agent.agent.Agent` entry point resolves to the runtime composition root. Internal imports use the actual module locations. Moving Python modules does not rename SQLite tables or change their serialized contents.

### Worker Dependencies

`BackupContext` and `RestoreContext` expose only capabilities needed by workers. A backup handler can access its repository client, hooks, retention and relevant platform clients. A restore worker needs the restic client and repository configuration. Neither context exposes the socket connection, scheduler or arbitrary runtime internals.

Runtime helpers receive focused dependencies: the scheduler gets tables and the executor submission function; reporting gets the request callback; secret processing gets keys, caches and the required persistence/transport operations. This permits meaningful tests without booting the entire agent or downloading tools.

### Durability And Concurrency

- The backend owns each agent's UUID; registration and sync persist it in the existing agent configuration. Numerical IDs remain authentication/database identifiers. Restic uses `drastic-<UUID>` only as explicit backup metadata, never as an implicit snapshot filter. File/TrueNAS parents are selected by job/artifact tags; native CBT retains its confirmed source/checkpoint checks. Retention deletes only reconciled snapshot IDs.
- Manual commands and schedules share the bounded executor and job/repository resource admission.
- Asynchronous commands reserve operation history before submission and deduplicate repeated UUIDs. Cancellation and admission retain their shared fence.
- Schedule slots are claimed persistently before execution. One-shot claims survive history cleanup, restarts and clock changes.
- Opt-in backup retries belong to job schedules or chains. The agent scheduler polls futures and keeps waiting attempts only in memory, releasing executor resources between attempts; a restart discards them. Durable schedule-slot claims still prevent duplicate starts. Chains use their existing persisted step state and dispatch fences.
- Configuration sync replaces its tables in one local transaction, so an offline agent retains the last complete configuration on failure.
- Each account stores one IANA `schedule_timezone` (default UTC), configured in User Settings. Protocol 24 synchronizes it atomically with agent configuration into the existing settings table. Job and backend chain calendars use this zone through Common's shared scheduling rules; periodic intervals and durable minute claims remain UTC-based. Missing spring wall times are skipped and repeated autumn wall times match only their first occurrence. Process-local time zones do not select the schedule zone.
- Chain admission locks the chain row for manual and scheduled starts. A scheduled one-shot is disabled in the same transaction as its running or busy/skipped occurrence, so a later time-zone change cannot replay it. Other enabled schedules are preserved.
- Agent startup rejects database schema versions newer than its supported migration head before running migrations or recovering operations. Program rollback does not imply rollback of durable agent state.
- Reports are snapshotted while locked, then sent without holding the lock during network I/O. Only the acknowledged log sequence advances; concurrent updates stay dirty.
- Child reports do not overtake their outstanding parents. Failed sends rotate so unrelated reports can progress.
- A request timeout does not tear down a connection that may already have reconnected.
- Agent updates reuse the independent installer. Admission stays paused while its outcome is uncertain.
- Native Linux and macOS share bootstrap, release staging, registration, activation and rollback. Only service lifecycle differs: systemd on Linux, launchd on macOS. A separate one-shot launchd job owns Mac web updates; a file lock tracks installer/report liveness on APFS. Failed Mac stops retain the departing PID until exit is confirmed, including across retries. Targets predating the macOS lifecycle contract are rejected before stopping the active release.
- Service definitions are rendered by the activated lifecycle controller. macOS ownership checks validate the label, private program paths and file permissions rather than freezing the full plist template. Its controller captures service/updater output with bounded standard-library rotation, forwards shutdown signals and waits for the worker's exit.
- The installed lifecycle controller lives in `bin/installer.py`, independently of `current/source`, so an explicit agent downgrade cannot restore legacy ref-pinned updates. Target controllers declaring `INDEPENDENT_LIFECYCLE` may replace it; controller/wrapper files share the existing activation rollback. Bootstrap obtains the controller from the backend's public `/install.py` when the selected source lacks that contract. Targets must contain the supported Python restic downloader; unsupported historical targets are rejected before activation. Git and local installs retain only agent/common sources, required build files and lifecycle/dependency scripts, recording versions from the untouched checkout before discarding temporary Git sources.
- Managed installation/update targets are exact backend commits exposed through public, uncached build metadata. The installer verifies the fetched commit and stages its pinned restic executable before stopping the active release. Unknown or dirty backend builds require an explicit local source override; old lifecycle managers transition once through the pipe installer or a Docker image update.
- Failed integrity checks keep retention blocked until the required verification succeeds. Restore path, snapshot ownership and overwrite checks remain in the execution services.
- Retention reads run membership from owned repository snapshots, not historical reports. Accounts have a stable `backup_owner_uuid`, atomically synchronized with the agent configuration and included in Recovery Export. Backup handlers add `drastic-owner` at creation through their shared tag helper. Only that owner with unambiguous job/run UUIDs is eligible; all unmarked legacy snapshots remain untouched. After listing snapshots, agents request current job UUIDs, default policy, known running operations and check state. Missing jobs use the default; other existing jobs are untouched, while the executing job uses its synchronized policy. Freeze the orphan set before subsequent refreshes. Group owned snapshots by job/run using the earliest snapshot timestamp, and recalculate expiration on retry. There is no repository configuration catalog or extra completion snapshot.
- Successful full data phases add `drastic-run-complete` to one existing data snapshot. `services/backup_completion.py` verifies the complete owner/job/run/artifact identity before tagging and journals intent in existing agent settings. `storage/snapshot_references.py` atomically reconciles local artifacts, outbox payloads and the native CBT checkpoint before clearing intent. Repository setup resumes pending work under existing repository admission; existing retention retry scheduling also discovers these intents. Terminal delivery stays fenced, with pending logs/status still visible, until references are resolved. Backend reports preserve the single immutable old-to-new ID mapping against replays. Parent selection includes the owner namespace. Separate owners sharing job/run/artifact UUIDs cannot affect each other's completion or cleanup.
- Retention keeps the newest completion-confirmed run in addition to the policy; without any confirmed complete run, the job's runs are left untouched. An untagged run is unknown, not proof of failure or success. Completion is read solely from repository snapshots, irrespective of report history. Database copies retain the same owner identity; independent installations must use distinct owners.
- All eligible runs count irrespective of success. Known running operations are excluded except the locally executing run after its backup phase; unreported offline completion cannot be inferred from tags alone. Repository check tokens require full verification after reported failures, with no forced initial full read. A full check only clears its observed token, leaving concurrent failures unresolved. Agent-local blocks and prune markers survive restarts. A different agent can remove remaining expired snapshots; after permanent agent loss following a completed forget, space reclamation waits for a later prune. Reports are independently deletable history and optional cleanup-result metadata. Existing retry/outbox mechanisms remain; there is no periodic discovery.
- Restore points group Drastic snapshots by repository/owner/job/run identity using the shared `restic/snapshots.py` helper, independently of report existence; unmarked legacy snapshots retain job/run grouping. Unknown snapshots remain individual points with plain-file restore. The restore UI selects a run first and a component for restoration. Manual deletion confirms and revalidates the entire point, then uses the existing durable asynchronous operation path and repository admission. Confirmed removed IDs reconcile matching owned artifacts through normal report ingestion, including when prune fails. No new database grouping or migration is needed.
- Report ingestion locks agent, repository and operation rows in that order within its transaction. Replay cannot erase confirmed deleted IDs or resurrect an already forgotten artifact. Historical deletion-receipt lookup is not needed for retention. Agent deletion locks the same parent and refuses running operations. These short database transactions do not reserve the repository while restic runs. Restic lock conflicts defer work without creating integrity-failure tokens. Failed backups with snapshots invoke cleanup independently of success hooks and retain their failure status.
- Log upserts, parent linking and cleanup reconciliation also use current locking reads after admission. Job/agent deletion refreshes report rows before adding metadata, and dispatch callbacks lock and refresh their leaf operation before merging status. Unconfirmed starts are not failed merely because time elapsed. Automatic retention and manual deletion register normal running-report delivery and checkpoint confirmed removals in local operation history before further batches or prune, allowing existing interrupted-operation recovery to replay them. Ingestion unions confirmed removal IDs so stale reports cannot erase them.

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
