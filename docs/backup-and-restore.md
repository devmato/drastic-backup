# Backup and Restore

## Dashboard and Agent Reports

The dashboard shows two charts for reports started in the last 24 hours across your agents. **Last 24 hours (Status)** groups all reports into successful, failed, warning, cancelled, and running results. **Last 24 hours (Types)** groups the same reports into **Backup**, **Restore**, **Agent update**, and **Other**. Other includes repository checks, retention, repository unlocks, repository statistics, agent synchronization, and commands. Reserved operations without a reported start time use their creation time.

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
- `type` -- Backup implementation. Current values are `file` and `proxmox`.
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
- Each VM creates one complete Restic snapshot with a versioned manifest, configuration and all block files. Unchanged data is referenced by Restic; restore does not require older backup runs. Normal operation-based retention remains applicable.
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

Configure **Connections > TrueNAS** on a protocol 14 agent running as a TrueNAS app to enable the **TrueNAS-Backup** job type. Browse pools, datasets, folders and files, using **+** and **−** to include or exclude paths. Parent dataset selections automatically include descendants, including datasets added later. The agent creates temporary ZFS snapshots, backs up the selected paths through Restic, and cleans up on completion or interruption. Each dataset has its own backup artifact and normal file restore support. See [TrueNAS](truenas.md) for required mounts, permissions and the deployment smoke test.

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

Jobs and backup chains share the same schedule editor with **Hourly, Daily, Weekly, Monthly, Yearly, Once, and Periodic** types. Only the fields needed for the selected type are displayed. Hourly schedules may restrict weekdays and an inclusive hour window (a window such as 22–02 crosses midnight; weekday restrictions apply to the actual execution day). Yearly schedules can select multiple months. Monthly and yearly schedules skip dates that do not exist, for example April 31; February 29 runs in leap years. There is no random delay.

The editor previews the next execution using the same shared logic as the actual scheduler. Calendar schedules and suitable periodic intervals use `croniter`. Other periodic intervals, such as 45 or 90 minutes, use a stable UTC interval anchored at 1970-01-01 00:00 plus the configured offset. They do not reset at midnight, restart, or backup completion. Frequency must be positive and the offset must be smaller than the frequency. **Once** uses the selected date and time and is not converted into an annually recurring cron expression; its agent-local claim remains stored after normal history cleanup.

**Job and chain calendar schedules use the account's time zone.** The first successful browser login initializes an unset account zone from the browser. Later logins preserve it. Set **Zeitzone** under **User menu > Settings > Preferences**, then save to change it manually. Until initialization, schedules use **UTC**. One setting applies to all of the account's jobs and chains; host/container time zones do not override it. Connected agents are notified after initialization or saving; offline agents keep their last successfully synchronized zone until the next sync. The preview is calculated on the agent for a job and on the backend for a chain, and includes the zone. Job previews require the agent to be online; editing remains possible without a preview. Periodic intervals remain anchored to UTC. Nonexistent calendar times during the spring DST transition are skipped; repeated wall times in autumn run only on the first occurrence.

**Account time zones (protocol 24):** pause schedules when updating a development installation, update the backend/database first, then agents, initialize/select the intended zone and synchronize before resuming. The unpublished `user_timezone` migration leaves accounts unset (`NULL`); UTC is only the effective fallback before initialization. Previous job schedules used the agent's local time and chains used UTC, so review saved wall-clock times, especially if agents previously used different zones. Schedule definitions and existing claims are not reset. Agents below protocol 24 cannot receive enabled job schedules or provide previews. Existing cron expressions remain stored and are interpreted as their corresponding simple type where possible; other expressions remain editable as an existing schedule.

Agents execute schedules from their last successful synchronization. The agent persistently claims each matching UTC minute slot, so a restart does not run the same schedule slot twice. A slot rejected because execution capacity or the job/repository resource is busy is recorded locally as `skipped` and is not retried. Interrupted claimed or started slots are marked failed after restart.

Scheduling has no catch-up behavior: if the agent was stopped or otherwise missed a cron minute, that occurrence is not run later. Server-side schedule changes take effect only after the next successful synchronization. Offline execution also has the repository limitations described under [Assign a Repository](#assign-a-repository).

### Automatic Backup Retries

Job schedules and backup chains can enable **Retry temporary backup failures** (agent protocol **18**). It is off by default. Configure **1–6 attempts in total** (default **3**, including the first run) and **1–1440 minutes** between attempts (default **10**). The fixed delay begins after a failed attempt, not at its start. Direct manual job starts do not retry; manually started chains use the chain policy.

Only explicitly recognized temporary Restic transport failures are retried. Cancelled jobs, permanent or unknown failures, pending snapshot cleanup, and warnings after a complete backup are not retried. Retries start the entire job again, including its configured commands; they do not resume a VM stream at its previous byte offset. Already stored blocks may be reused by Restic deduplication.

The agent keeps pending retries only in memory, without occupying a worker during the wait. An agent restart discards them; the next regular schedule runs normally. Removing or disabling the job schedule, or changing its repository, discards waiting retries after synchronization. Retries use the current job retention setting. Busy executors postpone a retry without consuming an attempt. A new occurrence of the same schedule is skipped while its previous run is still active or waiting to retry.

Attempts appear as ordinary operations, with the attempt number in their logs. Chain policy applies **per step**: the chain waits for that step's attempts before advancing, and never repeats already completed steps. Chains keep their existing persisted execution state; **Cancel Chain** also stops waiting retries. Each retry gets a new start deadline measured from its due time. A chain remains active during waits, so another scheduled occurrence may be skipped. Final step failures still allow subsequent steps to run.

### Backup Chains

Use **Jobs > Backup Chains > Add Backup Chain** to execute existing backup jobs in a fixed order, on one or multiple agents. Set a name and the maximum wait for each step to start. Add jobs and move them up or down to set the order. Each step has its own repository and optional repository check, and uses the job's retention policy. Agents require **protocol 24 or newer** for completion-tagged backups; update the backend first, then the participating agents.

In the chain's **Schedules** section, use **Add Schedule** to select a schedule type, its timing and activation, just like a job schedule. Add, edit or delete multiple independent schedules for the same chain, for example weekdays at 02:00 and weekends at 04:00. **Chain schedules use the account's time zone**, shown in the shared schedule editor and list. Each enabled schedule starts the entire chain with the same steps and backup settings; repository and check options remain configured per step. Chains without enabled schedules can still be started manually. Existing chains' single start times and activation are migrated to their first schedule entry.

Chains organize execution order: ordinary failed or cancelled individual jobs do not prevent the remaining jobs from being backed up. The next step starts after the preceding job has completed its backup, configured check, retention/prune, and completion actions. **An unsuccessful backup completion-tag step is the exception:** the backup retains its warning (or actual failure/cancellation), but the chain ends failed and skips remaining steps with a reason. It does not wait indefinitely or automatically resume after repository recovery. Start a new chain run manually or let its next schedule start one. Other post-backup warnings, such as failed statistics or retention, do not trigger this exception. The overall result retains warnings, failures and skipped steps. A chain is not a single application-consistent recovery point across machines.

Retention remains scoped to the **job and repository**, including backups made outside this chain. There is no chain-wide retention policy or special prune at the end. Failed cleanup is persisted locally and retried before the next backup in that repository, not on a timer. You can also start repository retention manually. Retries check the repository before deletion. After any failed data check, including a random sample, a **full-data check** must succeed before cleanup proceeds; a different passing sample cannot clear the block. Policy changes and deletions trigger synchronization for all owned online agents, including agents with chain-only jobs or pending cleanup. Offline agents receive changes on their next successful synchronization. Pending cleanup and retry logs appear in the originating backup report. A later successful cleanup does not erase the original backup warning.

**Start Chain** runs the whole chain on demand; starting an individual job does not trigger a chain. Only one run of a given chain may be active. If multiple enabled schedules match the same UTC minute, they trigger one run, not multiple runs. A scheduled occurrence while a prior run is active is recorded as skipped, without a backlog. Missed schedule minutes are not caught up. Changes to membership, order, targets and step options affect new runs; active runs retain their saved step configuration. Jobs themselves retain their normal current source configuration and commands.

One-shot chain entries are disabled atomically when their occurrence is recorded, including a busy/skipped occurrence. A restart or later account time-zone change cannot replay that consumed entry. Other schedules on the chain remain enabled. Explicitly re-enabling an entry is a new user action.

Offline or busy agents wait up to the configured start timeout, measured from when their step becomes eligible. A definitely unstarted job is then skipped and the next job is attempted. An unconfirmed start or a running job is **not** assumed to have stopped when its connection disappears; the backend reconciles its status before advancing. Starts are persistently identified and duplicate commands cannot run the same operation twice. Backend restarts resume open chain runs. An agent restart marks an interrupted backup failed, rather than silently rerunning it.

**Cancel Chain** prevents remaining steps from starting and requests cancellation of the active job. If admission is uncertain or the active agent is offline, cancellation waits for confirmation. Disabling a schedule prevents future starts from that schedule; other enabled schedules continue to apply. Schedule changes do not cancel an existing run.

The backend coordinates every chain, including chains on one agent. During a backend outage, an already started backup can finish if its repository remains directly reachable, but cleanup requiring a fresh job comparison and the next chain step wait. Native repositories still need the backend restic proxy. Independent job schedules retain their existing autonomous behavior and remain active when a job is added to a chain. When editing a job, its **Schedule > Backup Chains** section shows chain membership and links to the centrally edited chain.

A chain serializes its own steps, not all access to a repository. Independent schedules, other chains or external Restic processes can still use that repository. Restic locks protect exclusive maintenance, and failed cleanup remains pending rather than forcibly unlocking a repository.

Due chain slots are recorded independently of network dispatch. Up to four background dispatch tasks per backend process contact agents, so a slow or unreachable agent does not block admission of other scheduled chains. The existing database claims still prevent duplicate execution across workers.

Pending retention work fetches the current job UUIDs and default from the backend once after the initial repository scan. Before deleting snapshots or running a pending prune, it refreshes the context after the repository check. Missing responses, changed ownership or time zone, and unresolved integrity failures defer cleanup without deleting snapshots. The executing job uses its synchronized retention setting. Each attempt keeps the policies obtained at its start; the next attempt selects runs again using current policies and removes only remaining expired snapshots. Policy changes can therefore preserve remaining snapshots, but cannot restore ones already removed. Backend or repository failures leave cleanup pending; the operation log distinguishes response timeouts, disconnected agents and backend rejections. Warnings remain in the operation status and log, without an additional warning banner.

Schedules can optionally run a repository check after a successful backup. A basic check reads repository metadata only. Set `read_data` to values such as `1/10`, `5%`, or `100%` when restic should also verify stored data.

Manual job runs can use the same repository-check options.

## Repository Checks

Repository checks run `restic check` through an online agent that has access to the repository.

Use manual checks from the repository page when validating a new repository, after storage maintenance, or when investigating restore or backup issues. Use scheduled post-backup checks when the extra runtime and repository lock time are acceptable.

## Retention

Retention policies define how completed job runs with stored snapshots are kept after backups. Keep policies conservative until restores have been validated for the repository.

Select an optional **Default retention policy** on the Retention Policies page. Each job has one **Retention** dropdown under General: **Use default**, **Disabled**, or a specific policy. This applies to manual, scheduled and chain backups, separately in each repository. Changes to the default affect all inheriting jobs. Removing a custom policy disables retention on those jobs instead of switching them to the default.

Automatic retention and its retries belong to the originating backup report: their logs appear there, and they do not add separate history entries or dashboard counts. Without an applicable policy or pending prune, retention is skipped with a message in the backup log. Internal cleanup reports remain durable for replay and deletion acknowledgements.

To run retention independently, open **Agent > Actions > Run retention** (the third action), select an assigned repository and start the run. This requires **agent protocol 26 or newer**. The run applies the current effective policy of each existing job in that repository, including jobs on other agents of the same account; disabled policies preserve those backups. Deleted jobs use the default policy, if configured. All normal ownership, last-complete-run and integrity-check protections also apply. A solo run has its own history entry and an explicit result even when no policy applies or no snapshots have expired. Its retries remain attached to that entry.

After a backup, including a failed non-cancelled attempt that produced snapshots, the agent scans repository snapshots and asks the backend for the owner UUID, current job UUIDs, default policy, known running backup UUIDs and integrity-check state. No historical backup reports are queried. Failed attempts retain their failure status and do not run success hooks. Jobs absent from the complete job list use the default; jobs on offline agents or without enabled schedules still count as existing. Other existing jobs are untouched. The executing job uses its own synchronized policy. Without a default, backups of deleted jobs are preserved. There is no deletion catalog, daily discovery or automatic cleanup on job deletion.

New file, TrueNAS, Proxmox TAR and Proxmox Native/CBT snapshots carry `drastic-owner:<UUID>` alongside their existing job, operation and artifact tags. The owner is a persistent account UUID shared by its agents, independent of numeric database IDs, job/agent deletion and report history. Automatic retention selects only snapshots with this unambiguous owner and valid job/run UUIDs. Different owners and all unmarked legacy snapshots are untouched, even if their job UUID is still known. UUID namespaces are coordination metadata, not access control against clients with write access to the same repository.

Retention groups owned snapshots by job and run directly in the repository. The earliest snapshot timestamp is the run's calendar time. All stored runs count, including failed or partial ones; attempts without snapshots do not count. **The newest confirmed complete run is always kept in addition to the configured policy.** Newer partial runs therefore cannot evict the last confirmed complete run. If a job has no confirmed complete run in the repository, automatic retention leaves its runs untouched. Old runs without a completion tag are unknown, not retroactively declared successful. Once a newer confirmed complete run exists, the normal policy can expire those old runs. Disabled policies still preserve everything. Known running backup UUIDs are excluded, except for the executing run whose backup phase has already ended locally.

After every component's backup data has been saved successfully, the agent adds **`drastic-run-complete`** to exactly one existing data snapshot using `restic tag`. This confirms the data phase, not later checks, hooks or statistics; those can still produce warnings. Partial data backups and cancellation before completion do not receive the tag. No extra metadata snapshot is created. Retention reads the tag together with unambiguous owner/job/run/artifact identity directly from the repository and does not consult historical reports. Foreign owners, including another installation using identical job/run/artifact UUIDs, cannot confirm or expire the account's runs. A copied database retains its owner UUID and therefore represents the same backup identity; an independent installation needs its own owner namespace.

Restic tag changes replace snapshot metadata and its ID, not file contents. Before tagging, the agent durably records a focused completion intent in its existing settings table. It verifies the explicit source's full owner/job/run/artifact identity, confirms the rewritten snapshot's identity, tree and original ID, then atomically updates local artifacts, outbox data and the affected native CBT checkpoint. A crash between Restic's save and remove can leave two metadata versions; recovery removes only the verified original. **Protocol 27:** a finished backup with an unresolved completion intent reports its actual result and end time, with a finalization-pending warning in the operation log instead of remaining `running`. Unresolved artifact IDs are withheld, and the durable report remains queued until reconciliation is acknowledged. The next backup in the same repository first recovers pending completions, then retries persisted cleanup before starting new data work. If completion recovery fails, no new backup data phase starts. A manual retention run also recovers pending completions. There are no timed repository retries and hooks are not replayed. Backend ingestion keeps the verified old-to-new mapping across late reports; stale reports cannot reactivate an acknowledged pending state. Backup parent selection also includes the owner namespace.

For a confirmed stale repository lock, use **Repositories > Unlock repository**, select an assigned online agent and unlock. Unlock deliberately bypasses completion recovery so it cannot be blocked by the task needing the lock removed. It uses normal Restic stale-lock removal, never forced removal of all locks, and does not start a backup or resume a failed chain. After unlocking, start a new backup/chain or retention run, or wait for the next scheduled backup. Until reconciliation is acknowledged, affected snapshots, reports, agents and repositories remain protected from deletion even though the backup is no longer running. The historical warning and failed chain remain unchanged after recovery.

**Upgrade to finalization recovery on the next run (protocol 27):** update the backend/frontend first, then the agents. No database or snapshot migration is required. Existing completion/cleanup intents and queued reports are reused; their old retry timestamps are ignored. Older agents keep their previous timed-retry behavior until updated, and newer agents cannot connect to an older backend.

Deleting a job or agent preserves its reports and artifacts. Agent deletion is refused while it has running operations or unresolved backup finalization. Reports retain their owning user and remain accessible in the global operation history, with the deleted agent's recorded name. Finished reports can be deleted manually without affecting marked snapshots' restore or retention; running operations, unresolved backup finalization and active chain references prevent report deletion. Recovery Export includes the backup owner UUID: preserve it and the job UUIDs when reconstructing the account before resuming cleanup. Copying the database preserves this identity rather than creating an independent namespace. Back up the database if report history is needed too; the human-readable export does not include it.

Backend failures defer cleanup and leave it queued for retry. An unavailable response is never treated as an empty job list. The orphan set is fixed from the initial repository scan, so a job first seen during a later refresh cannot be mistaken for a deleted job. Directly reachable custom repositories can still back up during a backend outage; native repositories require their backend proxy.

Restic locks protect individual maintenance commands; there is no additional backend cleanup lock or stored deletion plan. Each attempt recalculates expiration from the owned snapshots still present. A different agent can remove remaining expired components after an interruption. The original agent's durable local prune marker also retries prune when all expired snapshots were already removed. If that agent is permanently lost after forget completed, unreferenced data is reclaimed by a later prune; it does not require inventing repository tombstones or consulting report history. Confirmed successful forget batches are checkpointed before the next batch or prune. Existing running-report and interrupted-operation replay deliver those removal IDs even after prune failure or restart; older reports cannot erase acknowledged removal IDs.

Before automatic cleanup, a basic repository check is performed; no initial full data read is forced. Reported check failures set a repository check token; local failures also remain recorded on the agent. Restic lock conflicts defer the command without creating an integrity block or requiring a full read. Cleanup requires a full read while a real check failure is unresolved. A successful full check clears only the token it observed, never a newer reported failure. This status is shared through the backend, not between independent Drastic installations using the same physical storage. Full-data checks can be run manually or through the existing backup-check options. Restic forget/prune does not substitute for verifying all stored backup contents.

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

**Upgrade to composable rules (protocol 26):** pause schedules, upgrade the backend/database and all participating agents, then synchronize before resuming. The `retention_rules` migration converts each positive `keep_*` counter into a `last` rule, retaining count semantics and existing assignments. It removes the old counters; the API now accepts `name` and `rules` instead of `rtype`/`keep_*`. There is no runtime legacy-policy mode. Previously UTC-based buckets immediately use the account zone, which can change which backups are selected near boundaries. Agent SQLite schema 8 also migrates cached counters once. Older agents cannot synchronize, fetch a deletion context or receive new backup/retention commands. Already-running old autonomous schedules must be paused for the upgrade. Database downgrade is refused while retention policies exist because the old model cannot preserve the new rules and zone semantics.

**Upgrade to job retention (protocol 21):** pause schedules, upgrade the backend/database and all agents, then synchronize before resuming. Policies and backup data are preserved, but old schedule/chain assignments are deliberately discarded. Jobs start with **Use default**, and no default is selected automatically. Existing operation owners are backfilled; agent deletion now detaches reports instead of deleting them. Downgrading this migration is refused while reports of deleted agents exist, rather than deleting their history silently.

**Upgrade to report-independent retention (protocol 23):** the follow-up `backup_owner` migration generates a distinct namespace for every existing account. Upgrade all agents and successfully synchronize before resuming backups. New backups receive the owner tag at creation. Existing snapshots are not rewritten or automatically adopted: they remain visible and manually deletable in Restore, but are outside automatic retention. Agents without a synchronized owner refuse new backups instead of silently writing unmarked snapshots. Older cleanup-report and retry formats are no longer supported.

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

The platform boundary is `services/guest_backend.py`: `GuestFileBackend` owns
capability checks, disk presentation, guest access and workspace locking/cleanup.
The first implementation is `services/guest_linux.py`. `guest_disks.py` contains
platform-neutral `SnapshotFiles` (seekable file provider) and `DiskReader`
(`size`, `read_at(offset, length)`) contracts and TAR/native layout validation.
Other platforms can implement those contracts without duplicating backup layout
logic. Windows/macOS restore-agent backends are not implemented yet; guest OS
and browser OS are independent of the restore-agent backend.

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

The ordinary tests mock Proxmox tools and cover collision checks, command flags,
workspace ownership/expiry, failure cleanup, cancellation and symlink-safe file
export. The disk-reader tests index a sparse 1-TiB TAR disk with bounded reads and
exercise cross-block native reads and malformed/missing data. Pipeline tests
check binary transfer, both exit codes, spawn failure, timeout, cancellation and
children left by an exited producer. The real
Restic/FUSE checks run without Proxmox or guestfs when Linux, `/dev/fuse`,
`fusermount3`, Restic and system `python3-fuse` are available:

```bash
cd apps/agent
.venv/bin/python -m pytest tests/test_guest_disks.py tests/test_guest_linux.py -v
```

Those checks also expose EFI/TPM and import metadata and pipe all disk contents
into a checksum-verified target with less than 64 KiB allocated workspace files.
They use Python producer/consumer tools, not a real Proxmox VM import. The VM
restore unit tests additionally cover a 1-TiB logical source with only small
workspace metadata and target capacity/collision checks.

For a real guest filesystem round trip, run the opt-in agent integration test on a test
Proxmox node with `restic`, `vma`, `qmrestore` and the guestfs dependencies installed:

```bash
# A SMALL test VM on LVM-thin; creates and deletes a temporary disk snapshot:
export DRASTIC_TEST_PROXMOX_VMID=990000
export DRASTIC_TEST_GUEST_VOLUME=/dev/vg/root
export DRASTIC_TEST_GUEST_FILE=/etc/hostname
export DRASTIC_TEST_GUEST_SHA256=<sha256-of-the-original-file>
# These two settings authorize creating a stopped VM under this FREE VMID:
export DRASTIC_TEST_RESTORE_VMID=990001
export DRASTIC_TEST_RESTORE_STORAGE=local-lvm
cd apps/agent
.venv/bin/python -m pytest tests/test_proxmox_restore_integration.py -v
```

The tests stream the test VM's TAR snapshot into a temporary real restic repository,
restore/export a selected file and compare its SHA-256, and optionally restore
a whole stopped VM. Repeat with an unencrypted Windows/NTFS test VM and a
known file, for example `/Users/Test/Documents/restore-check.txt`, using its
volume device and a different free VMID. Use `--basetemp` on a sufficiently large
filesystem if the system temporary directory is too small. The test VMs are left
stopped for manual boot verification and subsequent removal in Proxmox.

Native modes have a separate, destructive-to-test-data-only opt-in check on a Proxmox test host:

```bash
# Requires root, local-lvm, Restic and native FUSE/libnbd dependencies.
# VMIDs 990103 and 990104 must be FREE. Only these newly created test VMs are removed.
DRASTIC_TEST_NATIVE_ROUNDTRIP=1 .venv/bin/python -m pytest \
  tests/test_proxmox_native.py::test_real_native_proxmox_roundtrip \
  --basetemp=/var/tmp/drastic-native-test -s
```

It tests automatic temporary storage selection, initial/full and CBT runs, a changed block, upload failure, lost bitmap, SIGKILL of the backup controller and Perl provider helper, pre-setup failure after a different completed target, checkpoint invalidation/recovery, pruning older backups and real streaming VM import with EFI/TPM. Source and restored disk data are compared by SHA-256, allowing target allocation padding. Recovery checks that QEMU remains running with the same process ID and releases the owned backup lock. It never selects VM 103 or another existing VM for writes, crash tests or restore. A real Windows boot/application recovery check is still required before relying on the new mode for production recovery.

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

Create a MariaDB dump while the stack is running:

```bash
mkdir -p control-plane-backup
docker compose -f docker-compose.yaml exec -T db \
  sh -c 'exec mariadb-dump --single-transaction --routines --events --triggers -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE"' \
  > control-plane-backup/drastic.sql
cp .env control-plane-backup/env
docker compose -f docker-compose.yaml images \
  > control-plane-backup/images.txt
```

Protect this directory as secret material. The SQL dump and master secret together expose encrypted control-plane settings, while repository recovery still requires a user's recovery password or a separately retained repository password.

Take a consistent copy or filesystem snapshot of repository and agent storage. Do not copy live repository files while backups, checks, forget, or prune operations are running. The simplest safe procedure is:

```bash
docker compose -f docker-compose.yaml stop backend rest-server
sudo tar -C /opt/drastic-server -czf control-plane-backup/restic-storage.tar.gz restic
sudo tar -C /opt/drastic-agent -czf control-plane-backup/agent-data.tar.gz data
docker compose -f docker-compose.yaml start rest-server backend
```

Replace the example paths with `DRASTIC_REST_SERVER_STORAGE_PATH` and `DRASTIC_AGENT_HOST_DATA_PATH`. A storage snapshot taken while the services are stopped is preferable for large repositories. Validate both the SQL dump and archive readability, copy the backup off-host, and test recovery regularly.

## Restore the Control Plane

Restore into an isolated host first. Use the same explicit application release tags and rest-server version that created the backup, then upgrade only after recovery is verified.

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

The app master secret and SQL dump alone cannot recover a repository password. Retain repository recovery passwords in a separate protected recovery system and test them before an incident.

On a recovery host, identify the repository subdirectory by locating the directory containing restic's `config`, `data`, `index`, `keys`, `locks`, and `snapshots` entries. Mount or copy that repository read-only for inspection, then run restic directly against its filesystem path:

```bash
export RESTIC_REPOSITORY=/recovery/native-repositories/<repository-directory>
export RESTIC_PASSWORD_FILE=/recovery/secrets/repository-password
restic snapshots
restic check --read-data-subset=5%
restic ls "<data-snapshot-id>"
mkdir -p /recovery/restore-target
restic restore "<data-snapshot-id>" --target /recovery/restore-target
```

Replace `<data-snapshot-id>` with the ID of the intended snapshot from the listing. In a shared repository, an unfiltered `latest` may belong to another job or VM. Use the job/run tags and an explicit ID to select the intended backup. Jobs with multiple VMs/datasets have multiple data snapshots; select the required artifacts of the intended run. Never restore directly over production data until the recovered files have been inspected. If only the rest-server endpoint is available rather than a filesystem copy, use the repository's original REST URL with `RESTIC_REPOSITORY=rest:http://...`, but prefer an isolated copy so emergency investigation cannot mutate the sole backup.
