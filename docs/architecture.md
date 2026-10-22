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

Deletion locks the owner's agents in ID order, then the repository, before checking running work. Managed Restic proxy requests hold shared agent/repository locks through streaming, so locally scheduled work cannot create a new storage lock between the deletion check and quarantine.

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

#### Admission and Scheduling

- Manual commands and schedules share bounded job/repository admission. Reserve history before submission, deduplicate operation UUIDs, and serialize cancellation with admission.
- Persist schedule claims before execution. One-shot claims survive history cleanup/restarts; busy slots are skipped, not queued for catch-up.
- Waiting agent retries exist only in memory and occupy no worker. Chains persist their own step state and use database leases/dispatch fences.
- Chain starts lock the chain row. Consume due one-shots in the same commit as their running or busy/skipped occurrence.
- Sync configuration, owner identity and effective zone in one SQLite transaction; failure retains the previous offline configuration.
- Account calendar rules use `schedule_timezone` (unset means UTC). Login initializes only a still-null zone after authentication; explicit choices win. Periodic intervals/claims use UTC; DST gaps are skipped and repeated wall times run once.

#### Identity and Reports

- Server-issued agent UUIDs identify Restic backup hosts, not implicit snapshot filters. Parent selection also checks owner/job/artifact identity; native CBT requires confirmed source/checkpoint state.
- Snapshot reports under their lock, release it for network I/O, and advance only acknowledged log sequences. Concurrent changes stay dirty; children cannot overtake outstanding parents. Rotate failed sends.
- One independent delivery thread keeps report timeouts out of the schedule loop. Log batches stay below the transport limit; terminal state is sent only in the last batch. Outbox replay starts at the first batch and deduplicates through the existing log sequences. Individual log messages retain at most 8192 characters, with explicit truncation markers.
- Ingestion locks **agent → repository → operation** and refreshes rows before merging logs, artifacts, parents and cleanup receipts. Deletion follows the same parent-lock ordering.
- Late dispatch callbacks must read current operation state. Timeouts do not prove non-execution and must not overwrite confirmed results or tear down a replacement connection.
- Union confirmed removal IDs on replay; never resurrect forgotten artifacts. Checkpoint removals before another batch or prune so crash recovery can replay them.

#### Completion and Retention

- `services/backup_completion.py` journals and verifies one completion-tag rewrite. `storage/snapshot_references.py` atomically replaces artifact, outbox and CBT references before clearing intent.
- Protocol 27 reports real execution end/result while `backup_completion_pending` protects unresolved references. Withhold unresolved IDs and retain the outbox until reconciliation and acknowledgement.
- `AgentOperation.blocks_cleanup` protects both running work and pending finalization. `backup_completion_failed` permanently fails the affected chain and skips later steps, even if reconciliation finishes before the next dispatcher tick.
- Recover completion, then pending cleanup, before the next backup's data phase in that repository. Explicit retention also recovers completion. No timed repository retries; unlock bypasses recovery and does not resume chains.
- Select whole runs from owned repository tags, never report history. Require unambiguous owner/job/run identities; foreign and unmarked legacy snapshots remain untouched. Independent installations need distinct owner UUIDs.
- Union validated retention rules in the account zone. Capture one evaluation time; freeze the initial orphan/policy set and refresh backend safety state after checking storage. Missing context, changed ownership/zone or unresolved integrity failures defer deletion.
- Preserve the newest confirmed complete run in addition to policy selection; without one, preserve all runs of that job. Failed/partial runs count; known running work remains protected.
- A recorded integrity failure requires a full check. Clear only the observed failure token, not concurrent failures. Lock conflicts defer maintenance without creating integrity blocks. Persist local retry/prune markers across restarts.
- Automatic retention uses durable child reports whose logs appear in the parent, without separate history/dashboard entries. Standalone retention has its own report and explicitly requests all owned job policies. Optional context extensions must not change legacy exact-key responses.

#### Restore and Updates

- Group restore points by repository/owner/job/run with Common's `restic/snapshots.py`; unmarked legacy runs retain job/run grouping. Unknown snapshots remain individual points.
- Confirm and revalidate the full point before deletion. Destination, symlink, overwrite and snapshot checks belong in execution services, not only the UI.
- Managed updates verify the exact backend commit and checksum-verified Restic before stopping the active release. Dirty/unknown builds require an explicit local source.
- `bin/installer.py` is independent of the selected agent source. Compatible controller/wrapper updates share activation rollback; downgrade must not restore ref-pinned legacy updates.
- Linux uses systemd; macOS uses launchd with a separate updater job, ownership/path checks, bounded logs and retained stop-PID evidence until exit is confirmed. See [Agent Installation](agent-installation.md#layout-and-updates).
- Keep admission paused while update outcome is uncertain. Executable rollback does not roll back persistent state; reject newer SQLite schemas before migrations or recovery.

See [Protocol Compatibility](agent-installation.md#protocol-compatibility) for current gates and [Retention](backup-and-restore.md#retention) for operating procedures.

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

`composables/useJobEditor.js` owns job/action/schedule saving. It validates protocol restrictions before the first write, does not mutate the form draft and refreshes shared state once after a write sequence. The existing child endpoints commit independently: on partial failure the editor refreshes persisted state, retains the draft and reports the original error. Acknowledged create IDs remain in the editor until it is reset for another job, so retries update already-created items and can remove them when the draft changes. It does not claim a transaction across multiple HTTP requests.

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
