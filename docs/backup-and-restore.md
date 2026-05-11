# Backup and Restore

## Create a Repository

Use a native repository when dRastic should manage storage through the integrated `rest-server`.

Use a custom repository when backups should go to an existing restic target. Configure the repository location and any provider-specific environment values required by restic. Repository passwords can be generated automatically or set manually.

Repository passwords are protected by the signed-in user's recovery key and are provisioned to agents when they need repository access. Revealing a repository password in the UI requires re-entering the account password.

## Assign a Repository

Agents can only use repositories assigned to them. Assign the repository to the agent before running jobs or schedules.

When a job is run manually, dRastic also synchronizes repository access to the selected agent.

If repository secret operations are locked, enter your account password in the confirmation dialog. This restores the recovery key in browser memory without leaving the current page.

Agent synchronization hydrates assigned repository keys into agent memory. Agents do not store repository keys in their data directory; after an agent process restart, backend sync must complete before repository jobs can access their repositories. If a local repository key needs to be recreated, the agent requests its existing recovery envelope from the backend and decrypts it locally. The password confirmation dialog is only needed when the backend must create or refresh a user-protected repository secret or a missing agent-specific envelope.

## Backup Job Types

Jobs define what an agent backs up. The repository is selected when a job is run manually or through a schedule.

Common job properties:

- `name` -- Human-readable job name shown in the web UI.
- `agent` -- Agent that executes the backup.
- `type` -- Backup implementation. Current values are `file` and `proxmox`.
- `config` -- Type-specific settings described below.
- `actions` -- Optional commands or Docker actions attached to lifecycle hooks.
- `schedules` -- Optional recurring runs with repository and retention settings.

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
- Excludes are passed to restic from `exclude_patterns`.
- Snapshots are tagged with `job_uuid:<job-uuid>`, `run_uuid:<run-uuid>`, `artifact_uuid:<artifact-uuid>`, and `artifact_key:default`.
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

### Proxmox Backup

Proxmox backup jobs stream QEMU guests from a Proxmox host into restic using `vzdump`.

Use this type when the agent runs directly on a Proxmox node and should back up VMs without writing intermediate archive files.

Required agent prerequisites:

- The agent must run on the Proxmox host.
- `vzdump` must be available on the agent host.
- Proxmox API credentials must be configured on the agent.
- The configured API token must be able to list nodes, list QEMU guests, read QEMU configs, and run the required backup operations.

Agent environment variables:

- `DRASTIC_PROXMOX_API_URL` -- Proxmox API base URL, for example `https://proxmox.example.net:8006/api2/json`.
- `DRASTIC_PROXMOX_TOKEN_ID` -- Proxmox API token ID.
- `DRASTIC_PROXMOX_TOKEN_SECRET` -- Proxmox API token secret.
- `DRASTIC_PROXMOX_NODE` -- Optional node name override. Required when the node cannot be detected automatically.
- `DRASTIC_PROXMOX_VERIFY_TLS` -- Set to `true` to verify Proxmox TLS certificates.

Configuration:

- `selection_mode` -- Guest selection strategy. Supported values are `all` and `include`.
- `guest_ids` -- List of selected VMIDs. Required only when `selection_mode` is `include`.

Selection modes:

- `all` -- Back up every supported QEMU guest discovered on the Proxmox node.
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

Runtime behavior:

- The agent discovers supported guests through the Proxmox API.
- For each selected guest, the agent runs `vzdump` in snapshot mode and streams stdout into restic.
- The command uses `--compress 0` because restic handles storage and deduplication.
- VM snapshots are tagged with `job_uuid:<job-uuid>`, `run_uuid:<run-uuid>`, `artifact_uuid:<artifact-uuid>`, `artifact_key:vm:<vmid>`, `source:proxmox`, `guest_type:qemu`, `vmid:<vmid>`, and `backup_method:vzdump`.
- A JSON manifest is stored for each VM with tags including `kind:manifest`, its own `artifact_uuid`, and `artifact_key:vm:<vmid>:manifest`.
- After successful guest backups, optional repository checks, retention, and repository statistics are run.

The backup stream command is equivalent to:

```bash
vzdump <vmid> --mode snapshot --stdout --compress 0 --node <node>
```

## Actions

Actions are optional lifecycle hooks attached to a job.

Actions are a high-trust Homelab feature. Command actions run shell commands on the agent host, and Docker actions control containers reachable from the agent.

Hooks:

- `start` -- Runs before repository initialization and backup execution.
- `error` -- Runs when repository initialization or backup execution fails.
- `success` -- Runs after backup and optional retention finished without failure.
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
2. Configure the Proxmox API variables on the agent.
3. Select an online Proxmox-capable agent.
4. Create a Proxmox backup job.
5. Choose all supported guests or select specific VMIDs.
6. Save the job and run it manually or add a schedule.

## Create a Schedule

Schedules connect a job, repository, and optional retention policy.

Use schedules for recurring backups. Run jobs manually for one-off validation after creating or changing a job.

Agents execute schedules from their last successful synchronization. If the server is unreachable, already synchronized schedules can continue running on the agent; server-side changes take effect after the agent reconnects and synchronizes.

Schedules can optionally run a repository check after a successful backup. A basic check reads repository metadata only. Set `read_data` to values such as `1/10`, `5%`, or `100%` when restic should also verify stored data.

Manual job runs can use the same repository-check options.

## Repository Checks

Repository checks run `restic check` through an online agent that has access to the repository.

Use manual checks from the repository page when validating a new repository, after storage maintenance, or when investigating restore or backup issues. Use scheduled post-backup checks when the extra runtime and repository lock time are acceptable.

## Retention

Retention policies define how successful job runs are kept after backups. Keep policies conservative until restores have been validated for the repository.

The agent tracks job runs and their backup artifacts. Retention keeps runs according to the configured `keep_last`, `keep_hourly`, `keep_weekly`, `keep_monthly`, and `keep_yearly` values, then forgets snapshots belonging to pruned runs.

Retention runs `restic forget --prune` for removed artifacts, so removed snapshots also free unused repository data. Prune can take longer than metadata-only retention and may hold repository locks while it runs.

## Restores

Restore operations run through the agent. The agent needs access to the selected repository and enough filesystem permissions for the restore target.

Before restoring into production paths, prefer a temporary restore location and validate the restored data.
