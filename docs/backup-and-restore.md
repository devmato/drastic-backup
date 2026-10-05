# Backup and Restore

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

Configure credentials under **Agents > your agent > Connections > Proxmox**, or use **Configure Proxmox** in the job form. Test the connection and save while the agent is online. Settings apply immediately to all Proxmox jobs on that agent, and the token secret is stored encrypted on the agent.

Agent environment variables remain a fallback when no configuration has been saved through the web UI:

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

## TrueNAS Backups

Configure **Connections > TrueNAS** on a protocol 3 agent running as a TrueNAS app to enable the **TrueNAS-Backup** job type. Select datasets and enable **Include child datasets** to include descendants automatically, including datasets added later. The agent creates temporary ZFS snapshots, backs up their files through Restic, and cleans up on completion or interruption. Each dataset has its own backup artifact and normal file restore support. See [TrueNAS](truenas.md) for required mounts, permissions and the deployment smoke test.

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

Schedules connect a job, repository, and optional retention policy.

Use schedules for recurring backups. Run jobs manually for one-off validation after creating or changing a job.

Agents execute schedules from their last successful synchronization. The agent persistently claims each matching UTC minute slot, so a restart does not run the same schedule slot twice. A slot rejected because execution capacity or the job/repository resource is busy is recorded locally as `skipped` and is not retried. Interrupted claimed or started slots are marked failed after restart.

Scheduling has no catch-up behavior: if the agent was stopped or otherwise missed a cron minute, that occurrence is not run later. Server-side schedule changes take effect only after the next successful synchronization. Offline execution also has the repository limitations described under [Assign a Repository](#assign-a-repository).

Schedules can optionally run a repository check after a successful backup. A basic check reads repository metadata only. Set `read_data` to values such as `1/10`, `5%`, or `100%` when restic should also verify stored data.

Manual job runs can use the same repository-check options.

## Repository Checks

Repository checks run `restic check` through an online agent that has access to the repository.

Use manual checks from the repository page when validating a new repository, after storage maintenance, or when investigating restore or backup issues. Use scheduled post-backup checks when the extra runtime and repository lock time are acceptable.

## Retention

Retention policies define how successful job runs are kept after backups. Keep policies conservative until restores have been validated for the repository.

The agent tracks job runs and their backup artifacts. Retention considers `success` and `warning` runs for `keep_last`, `keep_hourly`, `keep_weekly`, `keep_monthly`, and `keep_yearly`. Successful artifacts from a marked partial failed backup can also be removed, but the failed run itself is not treated as a retained successful run.

Before forgetting a snapshot, the agent reconciles the local artifact with the repository and requires matching `operation_uuid` and `artifact_uuid` tags. A missing snapshot is recorded as already absent. A snapshot with missing or mismatched tags is skipped and makes the retention report a warning rather than risking deletion of an unrelated snapshot.

Retention runs snapshot `forget` without prune, records the forgotten artifacts, and then runs `prune` separately. If prune fails, a retry runs prune without forgetting the same snapshots again. Prune can take longer and may hold repository locks while it runs.

## Restores

Restore operations run through the agent. The agent needs access to the selected repository and enough filesystem permissions for the restore target.

The default `fail_if_exists` policy refuses to start when a selected destination already exists. `overwrite` must be requested explicitly, the web UI requires confirmation, and existing files may be replaced. Restore targets and include paths are normalized; the filesystem root, traversal segments, and symlink components in the target path are rejected.

If restic fails after writing has started, the operation remains `failed` and is marked with `partial_failure` and `destination_may_contain_restored_data`. dRastic does not roll back files already written. Inspect or clean the destination before retrying. Before restoring into production paths, prefer a temporary restore location and validate the restored data.

### Proxmox VM and Guest File Restore

Open **Backup Jobs → Restore** (the purple restore icon). Proxmox jobs offer
**Whole VM**, **Files from VM**, and **Archive files**. Snapshots show their source
VMID, guest name when available, and timestamp. VM modes exclude manifest snapshots
and archives known to have failed. Successfully backed-up VMs from a partially
failed multi-VM operation remain usable.

The target agent must be online, updated to agent protocol 2 or later, running
directly on the Proxmox node with root privileges, and assigned the source
repository. Another node can be selected; the original backup agent need not be
online. Local `pvesh`, `vma`, and `qmrestore` perform host operations, so restoring
does not require the original node's API credentials.

**Whole VM**

1. Choose the restore agent, repository and VM snapshot.
2. Select a free target VMID and an active storage supporting VM images. The
   original VMID can be reused only when it is free, including across the cluster.
3. Keep **Generate new MAC addresses** enabled for a separate recovered VM, or
   disable it when deliberately preserving the original network identity.
4. Start the restore. The agent downloads and verifies the VMA archive, then runs
   `qmrestore` without force/overwrite and without starting the VM.
5. Check the operation result and the restored VM configuration in Proxmox before
   starting it. Referenced bridges, ISO images and other host resources must be
   available on the destination node.

**Files from VM**

On detected Proxmox hosts, the native installer and updater attempt to install
`python3-guestfs` and `libguestfs-tools` automatically. They use root privileges,
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
apt-get install --no-remove --no-install-recommends python3-guestfs libguestfs-tools
libguestfs-test-tool
```

The agent invokes the host's `/usr/bin/python3` for the system libguestfs binding;
it does not install another Python package into its virtual environment.

1. Choose the agent, repository and VM snapshot, then **Prepare file browser**.
2. Wait for archive download, verification, extraction and filesystem inspection.
   Preparation is an asynchronous restore operation and can be cancelled.
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

Export preserves regular file contents, basic file permissions/timestamps and
symbolic links without following them into the host filesystem. It strips
setuid/setgid bits and reports skipped sockets, device nodes and FIFOs as a
warning. This is a file recovery export, not a Windows ACL/alternate-stream,
Linux ownership/xattr, or full filesystem metadata reconstruction.

**Workspace, progress and cleanup**

- Temporary data uses `$DRASTIC_AGENT_DATA_DIR/restore-work`. Set
  `DRASTIC_RESTORE_WORK_DIR` in the agent's environment to a dedicated absolute
  directory on a sufficiently large filesystem. Restart the agent after changing
  it. The directory is private to the agent and must not be shared by multiple
  agent instances.
- Whole-VM restore needs workspace for the VMA archive and destination storage
  for the disks. File browsing needs space for the archive plus extracted disks,
  even when exporting one small file. Capacity checks conservatively use the
  disks' logical sizes; allow additional space for the export itself.
- Prepared images expire after one hour without a browse/export request. They
  are removed after export (including failed/cancelled exports), when closing the dialog, on periodic
  expiry, and on agent restart. Active requests lock their workspace against
  cleanup. **Prepare again** recreates an expired workspace.
- The existing operation page shows phases, archive download progress and logs.
  **Cancel restore** stops active work and also applies between subprocesses.
- Failed/cancelled imports can leave a partial VM and allocated volumes; failed
  exports can leave files, including temporary `.drastic-*` files. The report
  identifies partial destination data. Inspect and clean it before retrying;
  dRastic never destroys a destination VM automatically.

### Proxmox Restore Integration Check

The ordinary tests mock Proxmox tools and cover collision checks, command flags,
workspace ownership/expiry, failure cleanup, cancellation and symlink-safe file
export. For a real round trip, run the opt-in agent integration test on a test
Proxmox node with `restic`, `vma`, `qmrestore` and the guestfs dependencies installed:

```bash
export DRASTIC_TEST_VMA=/path/to/uncompressed-test-vm.vma
export DRASTIC_TEST_GUEST_VOLUME=/dev/vg/root
export DRASTIC_TEST_GUEST_FILE=/etc/hostname
export DRASTIC_TEST_GUEST_SHA256=<sha256-of-the-original-file>
# These two settings authorize creating a stopped VM under this FREE VMID:
export DRASTIC_TEST_RESTORE_VMID=990001
export DRASTIC_TEST_RESTORE_STORAGE=local-lvm
cd apps/agent
.venv/bin/python -m pytest tests/test_proxmox_restore_integration.py -v
```

The tests back up the supplied VMA into a temporary real restic repository,
restore/export a selected file and compare its SHA-256, and optionally restore
a whole stopped VM. Repeat with an unencrypted Windows/NTFS test archive and a
known file, for example `/Users/Test/Documents/restore-check.txt`, using its
volume device and a different free VMID. Use `--basetemp` on a sufficiently large
filesystem if the system temporary directory is too small. The test VMs are left
stopped for manual boot verification and subsequent removal in Proxmox.

## Create a User Recovery Export

A signed-in user can generate a Recovery Export repeatedly from **User menu > Settings > Account & Recovery** after re-entering the account password. The downloaded ZIP contains a standalone `recovery.html` that works offline and includes plaintext repository passwords, provider environment values, and configuration for agents and assignments, repositories, retention policies, jobs and actions, and schedules.

Treat the export as secret material. Store it immediately as an encrypted attachment in Vaultwarden or an equivalent vault, replace older copies after configuration changes, and remove all plaintext local copies and downloads.

The export excludes the account password and hash, the user's internal recovery key, the app master secret, agent secrets, agent encryption keys, private SSH keys, sessions and tokens, operation and log history, and notification secrets. Retain these separate prerequisites and storage where relevant:

- SSH/SFTP access and private key material.
- Proxmox credentials and required host tools.
- Native repository storage.
- MariaDB and other control-plane backups.

The Recovery Export is a manual reconstruction aid. It is not currently an import file and does not replace MariaDB or control-plane backups, native repository storage backups, or the separate prerequisites above.

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
mkdir -p /recovery/restore-target
restic restore latest --target /recovery/restore-target
```

Use `restic ls <snapshot-id>` and an explicit snapshot ID when `latest` is ambiguous. Never restore directly over production data until the recovered files have been inspected. If only the rest-server endpoint is available rather than a filesystem copy, use the repository's original REST URL with `RESTIC_REPOSITORY=rest:http://...`, but prefer an isolated copy so emergency investigation cannot mutate the sole backup.
