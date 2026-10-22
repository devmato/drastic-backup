# Backup and Restore

## Dashboard and Agent Reports

- **Last 24 hours (Status):** successful, failed, warning, cancelled and running reports across your agents.
- **Last 24 hours (Types):** Backup, Restore, Agent update and Other (checks, standalone retention, unlocks, sync and commands).
- Automatic retention children are shown inside the backup report, not counted separately. Repository statistics are part of post-backup processing, not a standalone operation type.
- Reports without a start time use their creation time.

Click a chart segment or legend entry to open reports filtered by its status or type. The report list uses the exact UTC time boundaries from the dashboard: the start is included and the end is excluded. You can combine type and status filters on the report page. Refresh the dashboard to advance this 24-hour window. The dashboard's **Needs Attention** report warnings and failures use the same period.

The report page supports filters for report type/group, state, agent, job ID, and UTC start time. Filters are stored in the URL and retained when returning from report details. **Last 7 days** updates the period; **Clear filters** shows the full history within the current agent scope. The Jobs page shows all configured jobs; filter execution outcomes on the report page.

The owner-scoped HTTP endpoints `GET /api/agents/operations` and `GET /api/agents/operations/summary` accept optional `type`, `group=other`, `state` (including `attention` for warnings/failures), `agent_id`, `job_id`, `since`, and `until` query parameters. Timestamps are ISO 8601 UTC instants. The list returns compact report rows with agent display names; the summary returns counts by concrete type and state. The existing per-agent history and operation-detail endpoints retain their full response fields.

## Create a Repository

Use a native repository when dRastic should manage storage through the integrated `rest-server`.

Use a custom repository when backups should go to an existing restic target. Configure the repository location and any provider-specific environment values required by restic. Repository passwords can be generated automatically or set manually during repository creation.

For SSH/SFTP custom repositories, copy the executing agent's SSH public key from the agent properties dialog and install it on the target host. The SSH private key stays on the agent and is not stored by the backend.

Repository passwords are protected by the signed-in user's recovery key and are provisioned to agents when they need repository access. Revealing a repository password in the UI requires re-entering the account password. Changing a repository password after creation is not supported yet because it requires coordinated restic key rotation.

## Assign a Repository

Agents can only use repositories assigned to them. Assign the repository to the agent before running jobs or schedules.

Each agent manages its own SSH identity and `known_hosts` file. New SSH hosts are trusted on first use by that agent. If a host key changes, reset the affected agent's known hosts from the agent properties **Actions** tab and run the job again. If an agent SSH key is rotated, update the target host with the new public key first.

When a job is run manually, dRastic also synchronizes repository access to the selected agent.

If repository secret operations are locked, enter your account password in the confirmation dialog. This restores the recovery key in browser memory without leaving the current page.

Agent synchronization stores repository configuration and an agent-encrypted restic access key in the agent data directory; the plaintext key is kept only in memory. After a restart, the agent can decrypt an already synchronized key with its persistent private key without contacting the backend.

This permits an already synchronized schedule to run while the backend is offline only for a custom repository that remains reachable directly from the agent. The local agent identity and encrypted repository key must still be present. Native repositories always use the backend's authenticated restic proxy, so their jobs fail while the backend or proxy path is unavailable. Missing or invalid local key material also requires a successful backend synchronization or reprovisioning before a job can run.

## Backup Job Types

Jobs define what an agent backs up. The repository is selected when a job is run manually or through a schedule.

Common job properties:

- `name` -- Human-readable job name shown in the web UI.
- `agent` -- Agent that executes the backup.
- `type` -- Backup implementation: `file`, `proxmox`, or `truenas`.
- `config` -- Type-specific settings described below.
- `actions` -- Optional commands or Docker actions attached to lifecycle hooks.
- `schedules` -- Optional recurring runs with repository and check settings.
- `retention_mode` -- `default`, `disabled`, or `custom`; `retention_id` selects a custom policy.

### File Backup

File backup jobs run restic against files and folders that are readable by the selected agent.

Use this type for normal host filesystem backups, mounted application data, configuration directories, and other path-based backup sources.

Required configuration:

- `paths` -- List of files or directories to include. At least one path is required.

Optional configuration:

- `exclude_patterns` -- List of files, folders, or restic exclude patterns to skip.

Path entry properties:

- `path` -- Filesystem path or pattern value.
- `group` -- Entry type. Supported values are `file`, `folder`, and `pattern`.

Docker agent path handling:

- Docker agents mount the configured host root read-only at `/mnt/host` by default.
- A host path like `/home/app/data` should usually be configured as `/mnt/host/home/app/data`.

Runtime behavior:

- The agent runs a restic backup for the configured `paths`.
- Browser exclusions (`file` or `folder` entries) are literal path rules. The most specific rule wins, so an explicit include can reopen a subtree below an excluded parent while leaving its siblings excluded. A deeper exclusion narrows that subtree again; removing a rule restores inheritance. Include exceptions require agent protocol 16 and also apply to previously saved rules.
- Free-form excludes (`pattern` entries) are passed to restic after the browser rules.
- Snapshots are tagged with `drastic-owner:<owner-uuid>`, `job_uuid:<job-uuid>`, `operation_uuid:<run-uuid>`, `artifact_uuid:<artifact-uuid>`, and `artifact_key:default`. Fully saved runs receive the completion tag described under Retention.
- If the selected repository is not initialized yet, the agent tries to initialize it while provisioning repository access.
- New custom repository targets are initialized with the repository recovery password, then the agent adds its local restic key for ongoing scheduled access.
- After a successful backup, optional repository checks, retention, and repository statistics are run.

Example include paths:

```text
/mnt/host/etc
/mnt/host/home
/mnt/host/var/lib/app
```

Example exclude patterns:

```text
*.tmp
/mnt/host/var/cache/**
node_modules
```

With restic 0.19.1, explicitly selected files and folders take precedence over exclude patterns. For example, selecting `/data` with `*.key` excluded skips its `.key` files; explicitly selecting `/data/private.key` also backs up that file. Contents of selected directories remain filtered by the patterns.

### Proxmox Backup

Use the shared split-view browser to select the local Proxmox host or individual VMs. Click the host to browse its VMs. Selecting the host with **+** includes all supported VMs, including newly created ones; use **−** on a VM to exclude it and **+** to include it again. Without the host selected, **+** selects individual VMs and **−** removes them from the selection. The right column shows the selected host or VMs and any active exclusions.

**Backup Mode** is below the split view; optional temporary backup storage remains under **Advanced**. Existing jobs retain their selection: all-guest jobs appear as a selected host with exclusions, while specific-guest jobs show their selected VMs.

Proxmox backup jobs support three job-level modes. Existing jobs default to **Snapshot**:

| Mode | Behavior |
| --- | --- |
| Snapshot | Existing LVM-thin snapshot → complete RAW/TAR stream into Restic. |
| Native — read all disks | Proxmox Backup Access with fleecing → read-only virtual 4-MiB block files → Restic. Always reads the complete disks. |
| Native — with CBT | The same native path; reuses unchanged disk blocks when the checkpoint and QEMU bitmap are valid. |

PBS is not required. Both native settings use one implementation and one backup layout. A missing tracking checkpoint causes a complete native read with a visible reason; missing native capabilities produce an error, not a silent switch to the Snapshot mode.

Use this type when the agent runs directly on a Proxmox node and should back up VMs without writing intermediate archive files.

Required agent prerequisites:

- The agent must run on the Proxmox host.
- The agent must run as root, with local `qm`, `perl`, `blockdev`, `lvs` and the installed Proxmox Perl modules available.
- Snapshot mode requires LVM-thin source disks. Native modes require protocol 8, the installed Proxmox external Backup Access API, `/dev/fuse`, `fusermount3`, `python3-fuse`, `python3-libnbd` and `libnbd-bin`. Native prerequisites are checked at runtime and reported by the agent. This integration is tested on PVE 9.2/QEMU 11 with LVM-thin disks.
- Native modes automatically select an active local LVM-thin storage with sufficient data and metadata headroom (agent protocol 9). Suitable VM source storages are preferred, then the most free capacity, with storage ID as a stable tie-breaker. **Advanced → Temporary backup storage** optionally pins a specific storage; an unavailable or unsuitable explicit choice fails instead of switching elsewhere. Fleecing holds old blocks overwritten during backup, not a preallocated full VM copy. Its growth still requires free data and metadata capacity.
- Proxmox API credentials must be configured on the agent.
- The configured API token must be able to list nodes, list QEMU guests and read QEMU configs. Snapshot operations use the local root agent, not the API token.

Configure credentials under **Agents > your agent > Connections > Proxmox**, or use **Configure Proxmox** in the job form. Test the connection and save while the agent is online. Settings apply immediately to all Proxmox jobs on that agent, and the token secret is stored encrypted on the agent.

Agent environment variables remain a fallback when no configuration has been saved through the web UI:

- `DRASTIC_PROXMOX_API_URL` -- Proxmox API base URL, for example `https://proxmox.example.net:8006/api2/json`.
- `DRASTIC_PROXMOX_TOKEN_ID` -- Proxmox API token ID.
- `DRASTIC_PROXMOX_TOKEN_SECRET` -- Proxmox API token secret.
- `DRASTIC_PROXMOX_NODE` -- Optional node name override. Required when the node cannot be detected automatically.
- `DRASTIC_PROXMOX_VERIFY_TLS` -- Set to `true` to verify Proxmox TLS certificates.

Configuration:

- `backup_mode` -- `snapshot` (default), `native` or `native_cbt`.
- `fleecing_storage` -- Optional local LVM-thin storage ID for native modes; empty or omitted means automatic selection. Available under **Advanced**, hidden for Snapshot mode. Explicit selection remains compatible with agent protocol 8; automatic selection requires protocol 9.
- `selection_mode` -- Derived from the browser selection: `all` when the host is selected, otherwise `include`.
- `guest_ids` -- List of selected VMIDs. Required only when `selection_mode` is `include`.
- `exclude_guest_ids` -- VMIDs excluded when `selection_mode` is `all`.

Selection modes:

- `all` -- Back up every supported QEMU guest discovered on the Proxmox node except `exclude_guest_ids`.
- `include` -- Back up only the VMIDs listed in `guest_ids`.

Guest ID rules:

- VMIDs must be unique.
- VMIDs must be integers greater than or equal to `100`.
- `include` mode requires at least one VMID.

Supported guests and disks:

- Current guest support is QEMU VMs.
- Backupable disks are detected from QEMU config keys such as `ide`, `sata`, `scsi`, `virtio`, `efidisk0`, and `tpmstate0`.
- CD-ROM media is skipped.
- Disks with `backup=0` are skipped.
- Path-based disk values without a Proxmox volume ID are skipped.

Runtime behavior for Snapshot mode:

- The agent discovers supported guests through the Proxmox API.
- Each VM gets one temporary Proxmox snapshot without RAM state. Proxmox coordinates the disks and uses the configured guest-agent freeze/thaw behavior.
- The agent reads the LVM-thin snapshot devices in a stable order into an uncompressed, deterministic TAR stream. Restic deduplicates, compresses and encrypts on the agent before sending new chunks to the repository.
- Each VM produces one Restic snapshot containing `qemu-<vmid>.tar`, with RAW disks, the saved VM configuration, optional firewall configuration and a versioned JSON manifest. EFI and TPM state disks are included when backupable.
- Restic tags include `job_uuid:<job-uuid>`, `operation_uuid:<operation-uuid>`, `artifact_uuid:<artifact-uuid>`, `artifact_key:vm:<vmid>`, `source:proxmox`, `guest_type:qemu`, `vmid:<vmid>` and `backup_method:snapshot`.
- Backup reads the complete disks again; this implementation has no changed-block tracking. Unchanged disk contents are deduplicated without being uploaded again.
- LVM-thin snapshots share existing blocks but retain old blocks overwritten during backup. The agent requires an estimated minimum of 20 GiB free data space per pool; set `DRASTIC_PROXMOX_MIN_FREE_GIB` to a positive integer on the agent to adjust this reserve. Invalid or non-positive settings use the default. Metadata usage must stay below 95%. Checks run before backup and via progress callbacks approximately every five seconds during streaming. The initial pool values are logged; insufficient data reserve, full metadata and missing/invalid measurements produce distinct errors. The reserve is a buffer, not a hard limit on snapshot growth.
- Cleanup intent is persisted before snapshot creation. Own snapshots are removed after success, failure or cancellation, at startup, and before the next Proxmox job. Locked VMs, foreign ownership and unknown creation outcomes remain pending rather than forcing deletion. Pending cleanup blocks further Proxmox backups.
- After successful guest backups, optional repository checks, retention, and repository statistics are run.

Only a small metadata plan and bounded streaming buffers are staged on the source node. Restic's repository index also consumes memory. Jobs are serialized on that node. Native repository traffic still uses the existing authenticated backend proxy; custom repositories remain directly reachable from the agent.

**Native runtime and tracking**

- The installed Proxmox backup lifecycle manages VM locks, EFI/TPM preparation, configured guest freeze/thaw, fleecing, NBD snapshot access and teardown. A process-local adapter registers no permanent Proxmox storage or plugin.
- Each VM creates one complete Restic snapshot with a versioned manifest, configuration and all block files. Restic references unchanged data; restore does not require older runs. Retention manages whole job runs directly from repository tags, independently of report history.
- Disks use stable block names, inodes and generation timestamps. The parent is selected explicitly from the confirmed checkpoint for this job/repository/VM. Changed blocks are read directly from the native NBD snapshot; fully consumed blocks are discarded in the temporary snapshot export to release fleecing capacity.
- EFI and TPM are read completely every run. CBT applies to large guest disks. A fresh job, missing parent, invalid bitmap, changed disk size/identity, changed QEMU session/host or unconfirmed interrupted run causes full reading/reinitialization. Migration or VM restart is handled conservatively by this fallback.
- Checkpoint reuse is invalidated before setup and confirmed only after successful Restic backup and native teardown. Failed uploads, partial Restic exit code 3, cancellation and crash windows cannot silently advance tracking. A completed backup with unconfirmed cleanup remains usable with a warning; its checkpoint remains invalid.
- A small durable agent-local journal tracks owned workspaces and helper processes. Startup and subsequent Proxmox jobs retry cleanup. Uncertain/foreign ownership remains pending instead of deleting unrelated VM resources.
- Recovery records setup intent and exact export/node ownership. It stops owned NBD exports and waits for their removal before tearing down Backup Access, so exported bitmaps are no longer busy. A pre-setup failure skips teardown when QEMU has no active backup access, even if a previous target ID remains cached. Foreign resources or failed NBD shutdown leave cleanup pending.
- Pool checks run through the existing process monitor at five-second intervals even when progress output stalls. Reserve exhaustion aborts through native failure cleanup. Fleecing is not a fixed-space guarantee.
- Storage selection is fixed during preflight for each VM backup and logged as automatic or configured. The selected pool is checked again before setup and monitored throughout the backup. No suitable storage causes a preflight error with the available capacity-check reasons.
- **Job details** shows requested/effective mode, full-read reason, actual disk bytes read, disk bytes reused, newly stored packed bytes and cleanup status. Disk I/O avoided and Restic deduplication are separate metrics.
- Native jobs show one **Data processed (all VMs)** progress bar. Preflight reads the selected volumes' logical sizes; each VM's frozen export corrects its contribution and adds the exact backup metadata size before transfer. Restic's processed bytes include both read and reused data, so CBT can advance the bar quickly. The bar remains visible across VMs and finalization; 100% means data processing is complete, while the operation status and phase indicate whether cleanup and post-backup work have finished. This is not an upload or elapsed-time percentage.

## TrueNAS Backups

Configure **Connections > TrueNAS** on a current agent running as a TrueNAS app to enable **TrueNAS-Backup**. Select pools, datasets, folders or files with **+ / −**. Parent datasets include future descendants. Each selected dataset is backed up from a verified temporary ZFS snapshot and supports normal file restore. See [TrueNAS](truenas.md) for mounts, permissions, cleanup and the required live smoke test.

## Actions

Actions are optional lifecycle hooks attached to a job.

Actions are a high-trust Homelab feature. Command actions run shell commands on the agent host, and Docker actions control containers reachable from the agent.

Hooks:

- `start` -- Runs before repository initialization and backup execution.
- `error` -- Runs when repository preparation, a prerequisite, a start action, or backup execution fails.
- `success` -- Runs after the backup completed and post-backup processing was attempted. A warning from check, retention, or statistics does not suppress this hook.
- `end` -- Runs at the end of the job regardless of success or failure.

Action modules:

- `command` -- Runs a shell command on the agent.
- `docker` -- Controls a Docker container reachable from the agent.

Command action properties:

- `command` -- Shell command executed on the agent.

Docker action properties:

- `container` -- Container name or ID.
- `action` -- Docker action. Supported values are `stop`, `start`, and `command`.
- `command` -- Command executed in the container when `action` is `command`.

Actions use `DRASTIC_TASK_TIMEOUT_SECONDS` for command execution timeouts.

## Backup Results

Backup reports use these final states:

- `success` -- The backup and all configured checks, retention, repository statistics, and hooks completed successfully.
- `warning` -- The backup completed, but a post-backup check, retention, statistics update, `success` hook, or post-backup `end` hook failed. Inspect the report because a retention warning can follow partially completed cleanup.
- `failed` -- Repository preparation, prerequisites, a `start` hook, or the backup itself failed. Partial multi-artifact backups can still contain individually successful snapshots and are marked as partial failures.
- `cancelled` -- Cancellation was requested; inspect cleanup results and any already saved snapshots before retrying.

A failed post-backup repository check changes the backup result to `warning` and skips retention for that run. Repository statistics plus the `success` and `end` hooks are still attempted. Retention failures also produce a warning rather than changing a completed backup to failed. Hook failures do not prevent the `end` hook from being attempted; failures before backup completion remain failed, while failures after completion are warnings.

## Create a File Backup Job

1. Select an online agent.
2. Create a file backup job.
3. Add the paths to back up.
4. Add optional exclude patterns.
5. Save the job and run it manually or add a schedule.

For Docker agents, host paths are normally available below `/mnt/host`.

Example paths:

```text
/mnt/host/etc
/mnt/host/home
/mnt/host/var/lib/app
```

## Create a Proxmox Backup Job

1. Run the agent on the Proxmox node.
2. Configure Proxmox access in the agent properties, test the connection, and save.
3. Select an online Proxmox-capable agent.
4. Create a Proxmox backup job.
5. Choose all supported guests or select specific VMIDs.
6. Save the job and run it manually or add a schedule.

## Create a Schedule

Schedules connect a job and repository. Retention is configured on the job.

Use schedules for recurring backups. Run jobs manually for one-off validation after creating or changing a job. **Agents > your agent > Actions > Perform backup** lets you select an existing job and target repository, then opens its operation report. **Show all repositories** includes repositories not yet assigned to the agent. The Run job button on the Backup Jobs page uses the same dialog with its job preselected.

Jobs and chains share these schedule types; there is no random delay:

| Type | Behavior |
| --- | --- |
| Hourly | Select minute, optional weekdays and inclusive hour window; 22–02 crosses midnight. Weekdays apply to the execution day. |
| Daily / Weekly | Selected local time, with weekdays for weekly runs. |
| Monthly / Yearly | Selected day; yearly can select multiple months. Nonexistent dates are skipped; February 29 runs only in leap years. |
| Once | One local date/time; its durable claim survives normal history cleanup. |
| Periodic | Positive minute interval anchored to the Unix epoch in UTC, plus an offset smaller than the interval. Restart, midnight and backup completion do not reset it. |

- Calendar rules use the [account time zone](user-guide.md#time-zone), not host/container `TZ`. An unset account uses UTC; offline agents retain their last synchronized zone.
- Spring DST gaps are skipped; repeated autumn wall times run only on their first occurrence. Periodic intervals remain absolute UTC intervals.
- Preview and execution use the same shared scheduling logic. Job previews need an online agent; chain previews use the backend. Editing does not require a preview.

When upgrading older agent-local schedules, review saved wall-clock times against the account zone. The `user_timezone` migration leaves accounts unset (`NULL`); UTC is the fallback until initialization. Existing schedule definitions and claims remain intact. Legacy cron expressions remain editable. Follow [Protocol Compatibility](agent-installation.md#protocol-compatibility) before resuming schedules.

Agents execute schedules from their last successful synchronization. The agent persistently claims each matching UTC minute slot, so a restart does not run the same schedule slot twice. A slot rejected because execution capacity or the job/repository resource is busy is recorded locally as `skipped` and is not retried. Interrupted claimed or started slots are marked failed after restart.

Scheduling has no catch-up behavior: if the agent was stopped or otherwise missed a cron minute, that occurrence is not run later. Server-side schedule changes take effect only after the next successful synchronization. Offline execution also has the repository limitations described under [Assign a Repository](#assign-a-repository).

### Automatic Backup Retries

Job schedules and backup chains can enable **Retry temporary backup failures** (agent protocol **18**). It is off by default. Configure **1–6 attempts in total** (default **3**, including the first run) and **1–1440 minutes** between attempts (default **10**). The fixed delay begins after a failed attempt, not at its start. Direct manual job starts do not retry; manually started chains use the chain policy.

Only explicitly recognized temporary Restic transport failures are retried. Cancelled jobs, permanent or unknown failures, pending snapshot cleanup, and warnings after a complete backup are not retried. Retries start the entire job again, including its configured commands; they do not resume a VM stream at its previous byte offset. Already stored blocks may be reused by Restic deduplication.

The agent keeps pending retries only in memory, without occupying a worker during the wait. An agent restart discards them; the next regular schedule runs normally. Removing or disabling the job schedule, or changing its repository, discards waiting retries after synchronization. Retries use the current job retention setting. Busy executors postpone a retry without consuming an attempt. A new occurrence of the same schedule is skipped while its previous run is still active or waiting to retry.

Attempts appear as ordinary operations, with the attempt number in their logs. Chain policy applies **per step**: the chain waits for that step's attempts before advancing, and never repeats already completed steps. Chains keep their existing persisted execution state; **Cancel Chain** also stops waiting retries. Each retry gets a new start deadline measured from its due time. A chain remains active during waits, so another scheduled occurrence may be skipped. Final step failures still allow subsequent steps to run.

### Backup Chains

1. Open **Jobs > Backup Chains > Add Backup Chain**. Participating agents need protocol **26** or newer; matching current builds are recommended.
2. Name the chain, set the maximum step-start wait, and add/reorder existing jobs.
3. Select a repository and optional check for each step. Retention comes from each job, not the chain.
4. Add any number of account-zone schedules, or use **Start Chain** for an on-demand run.

Execution rules:

- Each step waits for the preceding backup, checks, cleanup and completion actions. Ordinary job failures/cancellations do not stop later steps; the overall result retains them.
- **Completion-tag failure is the exception:** the chain fails and skips later steps. Recovery does not resume it; start a new run after resolving the problem. Other post-backup warnings do not trigger this exception.
- Only one run per chain is active. Matching schedules in one minute create one run; busy occurrences are skipped with no backlog/catch-up. Consumed one-shots are disabled even when skipped.
- Active runs retain saved membership/order/targets/options; jobs still use their current source configuration and commands. Independent job schedules remain enabled when adding a job to a chain.
- Offline/busy agents wait up to the step-start timeout. A definitely unstarted step is then skipped; an uncertain or running start must be reconciled before advancing. Backend restart resumes chain state; agent restart fails interrupted backups rather than replaying them.
- **Cancel Chain** fences later starts and requests active-job cancellation. Offline/uncertain admission waits for confirmation. Disabling a schedule alone does not cancel a run.
- The backend coordinates every chain. During its outage, an admitted backup can finish only if storage remains reachable; the next step and backend-dependent cleanup wait. Native storage still requires the backend proxy.
- A chain serializes its own steps, not unrelated schedules or external Restic clients. It is not an application-consistent recovery point across machines. See [Retention](#retention) for cleanup protections and retries.

Schedules can optionally run a repository check after a successful backup. A basic check reads repository metadata only. Set `read_data` to values such as `1/10`, `5%`, or `100%` when restic should also verify stored data.

Manual job runs can use the same repository-check options.

## Repository Checks

Repository checks run `restic check` through an online agent that has access to the repository.

Use manual checks from the repository page when validating a new repository, after storage maintenance, or when investigating restore or backup issues. Use scheduled post-backup checks when the extra runtime and repository lock time are acceptable.

## Retention

Retention keeps whole job runs per repository. Validate restores before enabling aggressive policies.

### Configure and Run

1. Create a policy on **Retention Policies**; optionally select it as the account default.
2. Under a job's **General > Retention**, select **Use default**, **Disabled**, or a specific policy.
3. Run a backup, or open **Agent > Actions > Run retention** and select an assigned repository.

- Job policies apply to manual, scheduled and chain backups. Removing a custom policy disables retention on its jobs rather than switching them to the default.
- After a backup, automatic retention processes that job and deleted jobs using the default. Other existing jobs are untouched. Failed non-cancelled backups with snapshots can also trigger cleanup; they remain failed and do not run success hooks.
- Standalone retention requires protocol **26** and applies all owned jobs' effective policies, including jobs on other agents. Deleted jobs use the default; without one, their snapshots remain.
- Automatic cleanup logs appear inside the originating backup report. Standalone runs have their own report; retries stay attached to that run.
- Without an applicable policy or pending prune, cleanup is skipped. Deleting a job does not itself start retention.

### What Is Protected

- Only snapshots with the account's `drastic-owner:<UUID>` and unambiguous job/run UUIDs are eligible. Foreign and unmarked legacy snapshots remain untouched.
- Runs are grouped from repository tags, not historical reports. Failed/partial runs with snapshots count; attempts without snapshots do not. The earliest snapshot time dates the run.
- **The newest completion-confirmed run is always kept in addition to the policy.** If none is confirmed, all runs of that job are preserved. An untagged run is unknown, not proof of failure.
- Known running backups and unresolved finalization are protected. The executing backup can be evaluated after its local data phase ends.
- **Disabled** preserves the job's snapshots. Jobs on offline agents still count as existing.
- A copied database retains its backup owner UUID. Do not run an independent installation with that copied identity against the same storage. Tags coordinate cleanup; they are not access control against repository writers.

### Finalization and Recovery

- After every component's data is saved, `restic tag` adds **`drastic-run-complete`** to one existing data snapshot. This confirms data completion, not later checks, hooks or statistics.
- Tagging changes the snapshot metadata ID. Durable intent and identity checks let the agent reconcile artifacts, queued reports and CBT references after a crash without rewriting backup contents.
- With protocol **27**, execution reports its actual result/end time even if reconciliation is pending. The log records the warning, unresolved artifact IDs are withheld, and the report remains queued until reconciliation is acknowledged.
- The **next backup in that repository**, or an explicit retention run, recovers completion before cleanup/new backup data. Failed recovery prevents a new data phase. There is no timed repository retry and hooks are not replayed.
- Pending references block deletion of affected snapshots, reports, agents and repositories. Later recovery does not erase the original warning or resume a failed chain.

For a **confirmed stale lock**:

1. Open **Repositories > Unlock repository** and select an assigned online agent.
2. Unlock uses normal stale-lock removal, not forced removal of every lock, and bypasses completion recovery.
3. Start a new backup/chain or retention run, or wait for the next scheduled backup. Unlock alone does not resume work.

### Safety and Failed Cleanup

- Cleanup requires a current backend context: ownership, complete job membership, policies, account zone, running operations and integrity state. Missing/invalid responses defer deletion, never imply an empty job list.
- A basic repository check runs before deletion/prune. A recorded integrity failure requires a **full-data check**; a passing random sample cannot clear it. Restic lock conflicts defer work without creating a new integrity block.
- Ownership/time-zone changes or newer check failures during verification defer deletion. Policies and evaluation time stay fixed for one attempt; the next attempt recalculates expiration.
- Confirmed deletion batches are persisted before more deletion or prune. Logs name the job (or short UUID), account-local time and removed snapshot count. `Partially removed` means only the reported batch count is confirmed; retries rescan actual storage.
- Prune failure does not restore removed snapshots. Pending cleanup/prune survives restart and is retried on the next repository run. After permanent agent loss, a later prune can reclaim already-unreferenced data.
- Finished report deletion does not affect restore or retention. Running/finalizing operations and active chain references remain protected. Job/agent deletion preserves owned history; Recovery Export does not include report history.

### Retention rules

Add or edit a policy on **Retention Policies**, then use **Add rule**, **Edit rule**, or **Remove rule**. Changes to the list are saved together with the policy; cancelling discards the draft. A policy requires at least one rule. Rules are combined with **OR**: a run is kept if any rule selects it. Rule order does not affect selection. All rules apply to whole job runs with snapshots, including partial runs; the last-confirmed-complete-run protection described above remains additional to the rules.

| Rule | Selection |
| --- | --- |
| Last N backups | The newest N runs, irrespective of success. |
| Last N backups, at most one per hour/day/week/month/year | The newest run in each occupied account-local calendar bucket until N runs are selected. Empty periods do not count; this can reach far back across gaps. The two repeated local hours at the autumn DST transition are distinct hourly buckets. |
| All backups in the last days/weeks/months | All runs at or after the combined cutoff, calculated from the start of the retention attempt. Calendar months are subtracted first, clamping to the last valid day, then local days and weeks. A local day can span 23 or 25 hours around DST. |
| All backups after a specific date | All runs strictly after midnight at the start of that date in the account zone. |
| One backup each day, for the last N days | The first run per local day within the current and preceding N−1 days. |
| One backup each week, for the last N weeks | The first run on the selected weekday per week within the current and preceding N−1 weeks. Weeks begin Monday. |
| One backup each month, for the last N months | The first run on the selected day per month within the current and preceding N−1 months. A missing date (for example February 31) is skipped. |
| One backup each year, for the last N years | The first run in the selected month per year within the current and preceding N−1 years. |

Calendar rules do not substitute a different day if the selected day has no backup. Unlike count thinning, calendar windows expire even without new backups when retention is run; the last complete run is still protected. For example, combine one backup each day for 7 days, each Sunday for 4 weeks and each month on day 1 for 12 months.

**Every time-based rule, including migrated policies, uses the effective account time zone.** An unset account (`schedule_timezone: null`) uses UTC, shown as **UTC (standard)**. A successful login may initialize the unset zone from the browser; a saved zone, including explicit UTC, is not overwritten. Changing the zone changes retention calendar boundaries as well as schedules. Missing/invalid retention-context zones or rules defer cleanup; they are not silently replaced. A zone change during the repository check also defers cleanup to the next attempt.

### Upgrading Older Retention State

Pause schedules, back up state, update backend/database then agents, and synchronize before resuming. See [Protocol Compatibility](agent-installation.md#protocol-compatibility).

| Migration | Required review |
| --- | --- |
| `job_retention` | Old schedule/chain policy assignments are removed. Jobs start with **Use default**, with no account default selected. Reassign policies explicitly. Downgrade refuses reports whose agents were deleted. |
| `backup_owner` | Accounts receive distinct owner UUIDs. Existing unmarked snapshots are not adopted or rewritten; restore/manual deletion remain available. Agents must sync their owner before backing up. |
| `retention_rules` | Positive `keep_*` counters become `last` rules; assignments remain. The API accepts `name`/`rules`, not `rtype`/counters. All time rules now use the account zone. Policies with no positive counters block migration and must be fixed first. |

Agent SQLite schema 8 migrates cached counters once. There is no runtime legacy-policy mode; MariaDB downgrade refuses existing rule policies rather than losing their semantics. Software-only rollback is not a state rollback.

## Restores

Restore operations run through the agent. The agent needs access to the selected repository and enough filesystem permissions for the restore target.

Open **Agents > target agent > Actions > Restore backup**. The target agent is preselected and can be changed. Choose an assigned repository and restore point. By default, points are filtered by the selected agent's UUID; opening Restore from a job additionally filters to that job. **Show all restore points** removes both filters, including other agents and jobs, older snapshots without an agent UUID and non-Drastic backups. Reopening the dialog restores the default filter. Drastic snapshots with unambiguous job/run UUID tags are grouped as one point, even without a report. For a multi-component point, select the VM or dataset to restore; single-component points need no additional selection. Unsupported or untagged backups use Plain file restore; recognized Proxmox TAR/Native/CBT backups retain their specialized modes. Snapshot grouping needs no data migration or tag changes.

The trash icon inside the restore-point field deletes the entire selected point after confirming its label and snapshot count. Foreign snapshots without a Drastic run identity are deleted individually. Backend and agent both recheck group membership and repository access; changed groups must be reloaded and confirmed again. A current database read after snapshot listing rejects newly reported running backups too. Deletion uses the existing running-report delivery, repository admission and Restic locks. Confirmed removals are checkpointed in local operation history after each batch and before prune; interrupted-operation recovery replays those IDs after restart. The dialog opens the existing operation page for progress and errors; reopening it reloads the snapshot list. Confirmed snapshot removals update matching retained reports even if subsequent prune fails. A prune failure does not restore deleted snapshots. Manual deletion of owner-tagged points requires agent protocol 23. Deleting unmarked points and plain-file restore without Drastic job tags still require protocol 22; update the backend before agents.

An uncertain dispatch remains uncertain across backend restarts until the agent reports admission or completion; elapsed time alone does not prove non-execution. A late timeout/error callback reads current operation data and cannot overwrite already confirmed results.

The default `fail_if_exists` policy refuses to start when a selected destination already exists. `overwrite` must be requested explicitly, the web UI requires confirmation, and existing files may be replaced. Restore targets and include paths are normalized; the filesystem root, traversal segments, and symlink components in the target path are rejected.

For **Plain file restore** (including Proxmox **Archive files**), **Target → Advanced** offers two independent Restic options. Both are off by default, can be combined, and require agent protocol 17 (update the backend before agents):

- **Match ownership by user and group names** passes `--ownership-by-name`. Restic matches the user/group names saved in the backup to local accounts instead of restoring numeric IDs. This can help when the same user has different IDs on the source and destination. It does not select the destination folder's owner or change how file permissions and ACLs are restored.
- **Skip restoring extended attributes** passes `--exclude-xattr '*'`. Restic omits all extended attributes, including ACLs stored as extended attributes and other extended metadata. Existing excluded attributes are left unchanged. This does not disable normal file permissions or guarantee that all platform-specific ACLs are ignored.

Enabled options appear in the operation details. These options apply directly to `restic restore`; they do not add permission post-processing and are not offered for **Whole VM** or **Files from VM** exports. A successful restore does not guarantee access for a particular local user: matching accounts, normal permissions and any remaining ACLs still matter.

If restic fails after writing has started, the operation remains `failed` and is marked with `partial_failure` and `destination_may_contain_restored_data`. dRastic does not roll back files already written. Inspect or clean the destination before retrying. Before restoring into production paths, prefer a temporary restore location and validate the restored data.

### Proxmox VM and Guest File Restore

Open **Backup Jobs → Restore** (the purple restore icon). Proxmox jobs offer
**Whole VM**, **Files from VM**, and **Archive files**. Snapshots show their source
VMID, guest name when available, and timestamp. VM and guest-file modes support
Snapshot/TAR and Native/CBT backups and exclude manifest snapshots, legacy VMA
snapshots, and backups known to have failed. Successfully backed-up VMs from a partially
failed multi-VM operation remain usable.

Legacy `vzdump`/VMA backups are no longer supported by the Proxmox VM or guest-file
restore paths. **Archive files** can still recover their original files through
ordinary Restic restore. VMA remains an internal streaming format for whole-VM restore;
no intermediate VMA archive is stored locally.

The target agent must be online and assigned the source repository. Protocol 10
enables on-demand TAR/native guest file restore; older agents require protocol 8
for native block backups and protocol 7 for TAR snapshots, and materialize guest
disks locally. Whole-VM restore requires an agent
running directly on the Proxmox node with root privileges. Guest file restore
uses the Linux/FUSE/libguestfs backend and does not require Proxmox host tools.
Another agent can be selected; the original backup agent need not be online.
Whole-VM restore needs FUSE, `python3-fuse`, and local `pvesh`, `vma`, and `qmrestore`.
It does not need libguestfs. These tools perform VM host operations, so restoring
does not require the original node's API credentials.

**Whole VM**

1. Choose the restore agent, repository and VM snapshot.
2. Select a free target VMID and an active storage supporting VM images. The
   original VMID can be reused only when it is free, including across the cluster.
3. Keep **Generate new MAC addresses** enabled for a separate recovered VM, or
   disable it when deliberately preserving the original network identity.
4. Start the restore. Metadata is validated and the backed-up disks, including
   EFI and TPM state, are exposed as read-only virtual RAW files. `vma create`
   reads these files and streams directly into `qmrestore -`; disk contents are
   read from Restic on demand and written directly to the destination storage.
   No complete local TAR, block staging directory, RAW images or VMA archive are
   created. `qmrestore` handles target allocation, configuration and stream
   validation, without force/overwrite and without starting the VM.
   Both producer and consumer must finish successfully; a failed source read,
   early EOF, timeout or cancellation fails the restore and stops the pipeline.
5. Check the operation result and the restored VM configuration in Proxmox before
   starting it. Referenced bridges, ISO images and other host resources must be
   available on the destination node.

**Files from VM**

On detected Proxmox hosts, the native installer and updater attempt to install
`python3-guestfs`, `libguestfs-tools`, `python3-fuse`, `fuse3` and native backup dependencies automatically. They use root privileges,
or ask through sudo when the dependency script is run with an interactive
terminal. Missing permissions, denied sudo authorization or package-manager
failures produce a warning and leave the agent usable without guest file restore.
The native installation itself still requires root/sudo.

Agent Properties displays missing GuestFS dependencies before opening the restore
dialog. This status is checked once per agent process and refreshed after an
update/restart; the restore dialog also performs a live check. Retry a native
agent update as root to install missing packages. When upgrading from an older
installer without this dependency step, the first update installs the new
installer; run the update once more if the warning remains.

For manual installation on the restore host:

```bash
apt-get install --no-remove --no-install-recommends python3-guestfs libguestfs-tools python3-fuse fuse3
libguestfs-test-tool
```

The agent invokes the host's `/usr/bin/python3` for the system libguestfs binding;
it does not install another Python package into its virtual environment.

1. Choose the agent, repository and VM snapshot, then **Prepare file browser**.
2. Wait for metadata validation and filesystem inspection. TAR and Native/CBT
   backups are read on demand via read-only virtual disks. Preparation is an
   asynchronous restore operation and can be cancelled.
3. Choose a guest filesystem/volume, then select files or directories. Linux LVM
   logical volumes and additional Windows NTFS partitions appear separately.
4. Choose an absolute destination directory on the agent and start the export.
   Paths are relative to the selected volume's root, not Windows drive letters or
   mountpoints from the guest's `/etc/fstab`.

The worker supports ext2/3/4, XFS, Btrfs, NTFS, FAT and exFAT when supported by the
host's libguestfs appliance. Encrypted, unsupported or unmountable volumes display
their reason and cannot be selected. Guest filesystems are opened read-only in
an isolated libguestfs appliance; no guest filesystem is mounted in the host
kernel. An appliance is started and closed for each browse/export request, so
directory listing can take several seconds.

For TAR/native backups, Restic serves seekable snapshot files through a private
FUSE mount. Uncompressed TAR disk members are accessed by their offset and size;
all disk slices share one archive handle for the entire request, including indexing,
so Restic's blob-offset index is not rebuilt on each read. Snapshot lookup and
metadata validation run in the timeout/cancellation-supervised disk helper.
Native disks are mapped to their existing 4-MiB block files. Only requested disk
ranges are read, though filesystem inspection, metadata and Restic blob sizes
can cause more data to be transferred than the selected file size. Manifests and
archive layout are validated; unread disk contents are not exhaustively checked
during preparation. Missing, short or inaccessible data fails the read rather
than substituting zeros. Repository access is required for each browse/export
request; a snapshot removed between requests is no longer browsable.

The restore agent must run Linux for guest-file browsing/export; Windows/macOS backends are not implemented. The guest OS and browser OS are independent of this requirement. Developer entry points are listed in [Project Structure](project-structure.md#agent).

Export preserves regular file contents, basic file permissions/timestamps and
symbolic links without following them into the host filesystem. It strips
setuid/setgid bits and reports skipped sockets, device nodes and FIFOs as a
warning. This is a file recovery export, not a Windows ACL/alternate-stream,
Linux ownership/xattr, or full filesystem metadata reconstruction.

**Workspace, progress and cleanup**

- Temporary data uses `$DRASTIC_AGENT_DATA_DIR/restore-work`. Set
  `DRASTIC_RESTORE_WORK_DIR` in the agent's environment to a dedicated absolute
  directory with space for metadata and working data. Restart the agent after changing
  it. The directory is private to the agent and must not be shared by multiple
  agent instances.
- Both restore modes use small workspace files and bounded streaming buffers,
  rather than full local disk/archive copies. Restic's repository index/cache
  and the guestfs appliance (file restore only) also need resources. Whole-VM
  restore still needs destination storage for the recovered disks; the capacity
  check conservatively uses logical disk sizes. File export needs space for the
  selected files. A separate restored VM needs its own destination disks in
  addition to any existing VM, but no extra full-size restore staging copy.
- On-demand mounts and helpers live only for a single request and are closed on
  success, failure or cancellation. A repository read lock is held while mounted,
  so prune may be blocked during active requests, not during idle sessions.
  Cleanup detects mounts through the Linux mount table, including disconnected
  FUSE endpoints left by a helper crash, before removing workspace directories.
- Prepared sessions expire after one hour without a browse/export request. They
  are removed after export (including failed/cancelled exports), when closing the dialog, on periodic
  expiry, and on agent restart. Active requests lock their workspace against
  cleanup. **Prepare again** recreates an expired workspace.
- The existing operation page shows phases, restored disk bytes and logs. Whole-VM
  progress comes from `qmrestore`'s logical disk-byte count, including zero ranges;
  it is not packed repository bytes or temporary archive size. Data progress at
  100% does not imply completion until both pipeline processes and cleanup finish.
  **Cancel restore** stops active work and also applies between subprocesses.
- Failed/cancelled imports can leave a partial VM and allocated volumes; failed
  exports can leave files, including temporary `.drastic-*` files. The report
  identifies partial destination data. Inspect and clean it before retrying;
  dRastic never destroys a destination VM automatically.

### Proxmox Restore Integration Check

See [Live Proxmox Checks](development.md#live-proxmox-checks) for opt-in Restic/FUSE, guest-file and full-VM round trips. Use disposable test data and free VMIDs. Automated checks do not replace booting a restored VM and verifying its applications.

## Create a User Recovery Export

A signed-in user can download an **AES-256 encrypted ZIP** from **User menu > Settings > Account & Recovery**, after confirming the account password. **Save the generated archive password before closing the dialog:** it is not stored or retrievable. The account password does not decrypt the kit; later account-password changes do not affect it. Leaving Settings during export cancels the browser download.

The kit requires an AES-ZIP-compatible tool such as **7-Zip**. Start with `recovery.html`; entry names are visible, all contents are encrypted, and extracted files contain plaintext credentials. Keep the kit/password independently available and replace the kit after configuration or credential changes.

The kit contains:

- Offline instructions, repository credentials, and configuration for agents/assignments, jobs/actions/schedules, retention policies and backup chains. Each repository has shell/PowerShell commands for listing snapshots, inspecting files and restoring.
- Repository password files at `repositories/<id>/password.txt`, used when no explicit Restic password source is configured. The generated commands clear inherited password sources and host filters, then restore the repository's configured `RESTIC_PASSWORD`, `RESTIC_PASSWORD_FILE` or `RESTIC_PASSWORD_COMMAND` when present, matching agent behavior. Referenced external files/commands must remain available separately. Setup lines can contain credentials; avoid saving them in shell history.
- Optional official Restic archives for **Linux x64/ARM64, macOS Intel/Apple Silicon, and Windows x64**, pinned to the server's version and bundled unchanged with verified checksums and license. Select only needed platforms. Downloads share a 240-second budget with short I/O timeouts, below the documented 300-second proxy timeout; a budget failure asks you to select fewer platforms and returns no partial kit. No selection means no external download. Export never requires an online agent.

### Recover directly from managed storage

Native storage is an ordinary Restic repository: `<DRASTIC_REST_SERVER_STORAGE_PATH>/native/<identifier>`. The kit records the exact suffix. The default host root is `/opt/drastic-server/restic`; `/data` is its container mount. Point Restic at the directory containing `config`, `data`, `index`, `keys` and `snapshots`, using the exported repository password. No running dRastic/rest-server is needed. Take a consistent storage copy with writers stopped; do not run init/forget/prune during recovery.

Follow the kit's commands and restore into a new empty directory. File/TrueNAS backups yield ordinary files; Proxmox Snapshot yields TAR/RAW files; Native/CBT yields manifest/configuration and 4-MiB blocks. VM import/guest-file access needs additional tools or dRastic. Native/CBT does not require older snapshots or the original checkpoint.

### Use a newly installed dRastic for recovery

Add the existing storage as a **Custom Repository** with the exported credentials, accessible from the recovery agent (including container mounts). A new Native Repository would allocate a new path. Assign/synchronize it, open **Actions > Restore backup**, and enable **Show all restore points**. Old jobs/reports are unnecessary; recognized Proxmox backups retain specialized restore modes when the agent has their dependencies. A new account can restore old snapshots, but its automatic retention does not manage the old account's owner-tagged backups.

Preserve backup storage, SSH access, externally referenced credential/certificate files and host tools separately. Account/master/agent secrets, private SSH keys, sessions/tokens, report/log history, notification secrets and Proxmox/TrueNAS connection secrets are excluded. This is a data-recovery/manual reconstruction kit, not a configuration import or a replacement for control-plane backups.

API clients of `POST /api/user/recovery-export` must supply `password` (current account password) and `archive_password` (a freshly generated 32-character base64url password). Optional `restic_targets` is a list containing `linux_amd64`, `linux_arm64`, `darwin_amd64`, `darwin_arm64` or `windows_amd64`. There is no plaintext fallback when the archive password is absent. The existing ZIP attachment response and `Cache-Control: no-store` remain unchanged. Do not log or persist either password.

## Back Up the Control Plane

A recoverable control-plane backup consists of all of the following from the same deployment:

- A MariaDB logical dump.
- `.env` and any external secret store, especially `DRASTIC_APP_MASTER_SECRET` and database credentials.
- Native repository storage at `DRASTIC_REST_SERVER_STORAGE_PATH`.
- Agent data directories at `DRASTIC_AGENT_HOST_DATA_PATH` if existing agent identities should survive recovery.
- The deployed Compose files and the exact server, agent image or native Git ref, and rest-server versions.

Before copying state:

1. Pause job/chain schedules, synchronize agents and let active backups, checks, restores and cleanup finish.
2. Stop **every agent** whose state is being copied. A backend outage alone does not stop cached schedules against custom repositories.
3. Stop backend and rest-server to prevent control-plane writes; leave MariaDB running for the dump.

Stop external Restic writers too before copying shared repository storage.

On each Docker agent host, from its deployment directory:

```bash
docker compose -f docker-compose.agent.yaml stop agent
```

Stop native agents through their host service manager instead. On the server host:

```bash
umask 077
mkdir -p control-plane-backup
docker compose -f docker-compose.yaml stop backend rest-server
docker compose -f docker-compose.yaml exec -T db \
  sh -c 'exec mariadb-dump --single-transaction --routines --events --triggers -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE"' \
  > control-plane-backup/drastic.sql
cp .env control-plane-backup/env
cp docker-compose.yaml control-plane-backup/
docker compose -f docker-compose.yaml images \
  > control-plane-backup/images.txt
sudo tar -C /opt/drastic-server -czf control-plane-backup/restic-storage.tar.gz restic
docker compose -f docker-compose.yaml start rest-server backend
```

On each stopped agent host, copy its data to a private backup location; for the default path:

```bash
umask 077
mkdir -p control-plane-backup
sudo tar -C /opt/drastic-agent -czf control-plane-backup/agent-data.tar.gz data
```

- Replace paths with your actual `DRASTIC_REST_SERVER_STORAGE_PATH` and agent state path. Include native `drastic-agent.env` and deployment/version metadata separately.
- Check command exit statuses and archive/dump readability before accepting the backup. Storage snapshots with writers stopped are preferable for large repositories.
- Restart agents using their deployment method, verify reconnection, then resume schedules. Missed occurrences are not caught up.
- Keep copies off-host and test restoration. These files contain authentication material; a complete agent-state copy can unlock synchronized repository keys. Retain account/repository recovery passwords independently too.

## Restore the Control Plane

Restore into an isolated host first, using the same application/rest-server versions. Keep cloned agents and the recovered backend off production networks; do not run two agents with the same copied identity. Upgrade only after recovery is verified.

1. Restore `.env`, preserving the original `DRASTIC_APP_MASTER_SECRET`, database names, users, and passwords.
2. Restore repository storage to `DRASTIC_REST_SERVER_STORAGE_PATH` and agent state to each `DRASTIC_AGENT_HOST_DATA_PATH`, with their original ownership and permissions.
3. Start only MariaDB and wait for it to become healthy.
4. Import the logical dump.
5. Start rest-server and backend, then inspect logs and sign in.
6. Start agents, confirm their existing identities reconnect, run `restic check`, and restore a sample into a temporary directory.

Example database import:

```bash
docker compose -f docker-compose.yaml up -d db
docker compose -f docker-compose.yaml exec -T db \
  sh -c 'exec mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE"' \
  < control-plane-backup/drastic.sql
docker compose -f docker-compose.yaml up -d rest-server backend
```

Import into an empty application database. If the target database already contains data, remove and recreate that database deliberately before importing rather than merging two control planes.

## Emergency Restore Without the Control Plane

Native repositories are ordinary restic repositories stored below `DRASTIC_REST_SERVER_STORAGE_PATH`. The UI, backend, MariaDB, and agent are not required for an emergency restore if you have:

- A complete, consistent copy of the native repository directory.
- The repository password retained before the incident.
- A compatible `restic` binary.

The app master secret and SQL dump alone do not unwrap the user-encrypted repository password. Retain repository recovery passwords in a separate protected recovery system and test them before an incident.

On a recovery host, identify the repository subdirectory by locating the directory containing restic's `config`, `data`, `index`, `keys`, `locks`, and `snapshots` entries. Mount or copy that repository read-only for inspection, then run restic directly against its filesystem path:

```bash
export RESTIC_REPOSITORY='/recovery/native-repositories/<repository-directory>'
unset RESTIC_PASSWORD RESTIC_PASSWORD_COMMAND RESTIC_HOST
export RESTIC_PASSWORD_FILE=/recovery/secrets/repository-password
restic --no-lock snapshots
restic --no-lock check --read-data-subset=5%
restic --no-lock ls "<data-snapshot-id>"
mkdir -p /recovery/restore-target
restic --no-lock restore "<data-snapshot-id>" --target /recovery/restore-target
```

`--no-lock` is only for this isolated, consistent copy with no writers; it permits inspection of read-only storage. A 5% check samples data, not the entire repository. Do not bypass locks on a live repository.

Replace `<data-snapshot-id>` with the ID of the intended snapshot from the listing. In a shared repository, an unfiltered `latest` may belong to another job or VM. Use the job/run tags and an explicit ID to select the intended backup. Jobs with multiple VMs/datasets have multiple data snapshots; select the required artifacts of the intended run. Never restore directly over production data until the recovered files have been inspected. If only the rest-server endpoint is available rather than a filesystem copy, use the repository's original REST URL with `RESTIC_REPOSITORY=rest:http://...`, but prefer an isolated copy so emergency investigation cannot mutate the sole backup.
