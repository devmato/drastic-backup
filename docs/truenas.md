# TrueNAS

Run one dRastic agent as a Custom App on each TrueNAS system. The dRastic server can be remote; the agent reads the NAS datasets locally and sends backups to its assigned repository.

The TrueNAS integration requires **TrueNAS 25.10 or newer and agent protocol 14**. Its API contract targets 25.10.4. Update the server before the agent. The native `/install` script is intended for writable Linux hosts, not the protected TrueNAS system filesystem.

## Install the agent app

1. In **Apps**, choose the pool that will store application images. TrueNAS manages its internal `ix-apps` dataset.
2. Create a user dataset such as **hddpool01/appdata**. A separate dataset for each app is optional: Compose creates the `drastic-agent` subdirectory automatically.
3. Open **Apps > Discover Apps > ⋮ > Install via YAML**.
4. Enter `drastic-agent` as the application name and paste the Compose below into **Custom Config**.
5. Replace the pool/dataset names, server address and dRastic registration credentials. The image version should match your server; use `:develop` for a development server.

```yaml
services:
  agent:
    image: ghcr.io/devmato/drastic-backup-agent:latest
    restart: unless-stopped
    environment:
      DRASTIC_SERVER: "https://backup.example.net"
      DRASTIC_USER: "YOUR_DRASTIC_USER"
      DRASTIC_PASSWORD: "YOUR_DRASTIC_PASSWORD"
    volumes:
      - /mnt/hddpool01/appdata/drastic-agent:/app/data
      - /mnt/hddpool01/daten:/mnt/host/mnt/hddpool01/daten:ro,rslave
      - /mnt/hddpool01/daten_regina:/mnt/host/mnt/hddpool01/daten_regina:ro,rslave
      - /mnt/hddpool01/dev_projects:/mnt/host/mnt/hddpool01/dev_projects:ro,rslave
      - /mnt/hddpool01/medien:/mnt/host/mnt/hddpool01/medien:ro,rslave
```

Write `$$` for a literal `$` in a Compose password. After successful registration, remove `DRASTIC_USER` and `DRASTIC_PASSWORD` and save the app again. Keep `/app/data`: it contains the agent identity, encryption keys, configuration and cleanup state. Protocol 4 images also support agent updates through dRastic's **Agent Properties > Actions > Update**, without a Docker socket mount. These updates survive container restarts; recreating the app returns software and system packages to the selected image while retaining `/app/data`. Update the image through TrueNAS to replace the base image or adopt this support in an older deployment. See [Docker Updates](agent-installation.md#docker-updates).

### Dataset and snapshot mounts

The default host-root mapping is `/mnt/host`. For a TrueNAS mountpoint `/mnt/hddpool01/daten`, the agent expects `/mnt/host/mnt/hddpool01/daten`. Mount every dataset you want to select, or mount the pool at `/mnt/host/mnt/hddpool01` with `:ro,rslave` to expose all its mounted datasets.

`ro` makes the source bind mount read-only. `rslave` propagates new host mounts into the running container. The agent opens each new snapshot directory through the TrueNAS API to trigger its host-side ZFS mount, then verifies that the container sees the exact `dataset@snapshot` ZFS mount. It fails rather than reading live data or an empty mount directory.

The host source mount must support propagation. You can inspect it in the TrueNAS shell:

```bash
findmnt -o TARGET,PROPAGATION /mnt/hddpool01/daten
```

If Docker rejects `rslave`, or the agent reports that a snapshot is not mounted, check the source mount's shared/slave propagation and the Compose paths. Do not work around this by granting the container privileged access or by making the TrueNAS system filesystem writable. A deployment must pass the snapshot smoke test below; the Docker/ZFS automount interaction cannot be verified by the API connection test alone.

## Configure the connection

1. In TrueNAS, create an API key for a user allowed to perform the operations below. Keep the key and the username that owns it.
2. In dRastic, open **Agents > your agent > Connections > TrueNAS**.
3. Enter the NAS HTTPS address reachable from the container, the username and API key. `localhost` inside the container is not the NAS host.
4. Keep TLS verification enabled with a trusted certificate; for a self-signed NAS certificate you can explicitly disable it under **Advanced**.
5. Use **Test connection**, then **Save**. Testing checks authentication, version and dataset discovery; it does not create snapshots or prove write/delete permissions.

The user needs access to:

| API method | Purpose |
| --- | --- |
| `system.version`, `system.host_id` | Version and NAS identity |
| `pool.dataset.query` | Discover filesystem datasets (`DATASET_READ`) |
| `pool.snapshot.query` | Reconcile snapshots (`SNAPSHOT_READ`) |
| `pool.snapshot.create` | Create temporary snapshots (`SNAPSHOT_WRITE`) |
| `pool.snapshot.delete` | Remove temporary snapshots (`SNAPSHOT_DELETE`) |
| `filesystem.listdir` | Open snapshot directories on the host (`FILESYSTEM_ATTRS_READ`) |

Use a TrueNAS role/privilege assignment that grants these methods. The API key inherits its owner's permissions. Normal file read/traverse permissions inside the container are also required; API access does not mount datasets or grant file access.

The API key is stored encrypted on the agent and is never returned by the settings API. Leaving its field empty keeps the existing key. Changing the NAS address or username requires entering a key again. Removing a connection disables its job type for new jobs; existing jobs remain visible.

## Create a TrueNAS backup job

Choose **Add job > TrueNAS-Backup** on the configured agent, select datasets or paths, and configure the normal repository, schedule and retention settings.

- Browse pools, datasets, folders and files in the same hierarchical browser used for file backups. Pools and datasets use the `storage` icon, folders `folder`, and files `description`, including in the selection on the right.
- Selecting a pool or parent dataset automatically includes all supported filesystem datasets below it, including newly created children. A selected folder also includes child datasets mounted beneath it. Overlapping dataset selections produce only one backup per dataset.
- Use **+** to include an entry and **−** to exclude it and its subtree. Use **+** on the excluded entry or remove its exclusion rule to include it again. Exclusions take precedence over selections. **Exclude patterns** under Advanced still apply relative to each dataset root; browser exclusions match literal paths, even if names contain wildcard characters.
- Existing jobs are migrated once to path selection, preserving their selected datasets, exclusions and patterns. Parent selections now always include child datasets, so jobs that previously disabled child inclusion may back up more data. Update the agent to protocol 14 before running or editing these jobs.
- Every included dataset must be unlocked, mounted and readable by the agent. An unavailable dataset fails validation before temporary snapshots are created. Zvols and internal system datasets are excluded from dataset discovery.
- The job creates a non-recursive snapshot of every selected dataset before reading files. Separate datasets are snapshotted sequentially, not atomically as a group.
- Exclusions are relative to each dataset root. For example, `cache/**` excludes a root-level cache; `**/cache/**` excludes nested caches too.
- Each dataset produces a separate Restic artifact in the operation report. Restore paths start at the dataset's contents, without temporary `.zfs/snapshot/...` prefixes. Successive backups reuse the previous backup of that dataset as their parent.
- The progress bar measures the current dataset, identified by its name and position in the job. Until its scan has finished, and during snapshot creation, cleanup and finalization, the bar runs without a percentage and the phase is shown below it. Dataset counters describe the current dataset; job counters accumulate across datasets. The animation indicates a running operation, not a measured transfer rate.
- ZFS snapshots provide a fixed filesystem state. Application-consistent database backups still require appropriate application preparation. Existing start/end actions remain available.
- One TrueNAS backup runs at a time per agent. A concurrent TrueNAS run fails with a busy message; existing schedules retain their normal no-catch-up behavior.

These are **file backups from ZFS snapshots**. Restore through dRastic's normal file restore workflow to a writable target, using a dedicated writable restore mount if needed. ZFS dataset properties, pool topology, encryption configuration, TrueNAS shares and NFSv4 ACL reconstruction are not recreated by this workflow. Preserve the TrueNAS configuration separately.

## Cleanup and interrupted runs

Temporary snapshot names contain the operation UUID. Snapshot ownership is marked with the agent ID and operation UUID. dRastic persists cleanup intent before requesting a snapshot and checks ownership and NAS identity before deleting anything.

Cleanup runs after success, failure or cancellation, at agent startup, and before the next TrueNAS backup. If cleanup fails, a successful backup becomes a warning and the pending snapshot remains recorded. Under **Connections > TrueNAS**, use **Retry cleanup** after resolving the problem. After an uncertain creation timeout, wait five minutes before retrying an absent snapshot's cleanup.

Connection changes are blocked during a running TrueNAS backup. Pending snapshots prevent changing the NAS address or removing the connection; you can still replace an expired API key for the same NAS. New TrueNAS backups wait for pending cleanup to be resolved. Other agents' snapshots and snapshots without matching ownership are never removed.

## Snapshot smoke test on TrueNAS 25.10.4

Before relying on a deployment:

1. Mount a small disposable filesystem dataset with `:ro,rslave`; create a file containing `before`.
2. Configure the connection and create a TrueNAS backup job for that dataset.
3. Start the job. After its log reports **Created and mounted**, change the live file to `after` while the backup is running. Use enough test data to leave time for this step.
4. Restore to a separate writable directory. The restored file must contain `before`.
5. Confirm that the temporary ZFS snapshot disappears. Repeat once to verify a second backup and restore.
6. Cancel a sufficiently long test run and verify cleanup. Restart the agent during another test run, then verify that its recorded snapshot is cleaned up after restart.

Automated tests cover the API boundary, encrypted settings, missing mounts, ownership checks, cancellation and a real Restic backup/restore using simulated snapshot contents. The live ZFS/Docker propagation test requires an actual TrueNAS host.
