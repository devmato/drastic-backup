# Agent Installation

Agents execute backups and restores. They authenticate against the server with a generated agent identifier and secret after registration.

Agents are high-trust components. A native agent usually runs as `root`, and a Docker agent with Docker socket access can effectively control the Docker host. Install agents only on hosts you trust with backup and restore access.

## Docker Agent

For TrueNAS Custom Apps, see the [TrueNAS installation and snapshot backup guide](truenas.md).

Use the install snippets in the web UI under **Agents > Add Agent**. Docker snippets use the default GHCR `latest` image or the server's `DRASTIC_AGENT_IMAGE` override and do not require a project checkout on the target host. Official GHCR agent images support `linux/amd64` and `linux/arm64`.

If you still run the standalone Compose file, create an environment file from the production example and configure the server URL and initial registration credentials:

```bash
cp .env.agent.example .env
```

Start the agent with Docker Compose:

```bash
docker compose -f docker-compose.agent.yaml up -d
```

Important agent paths:

- `DRASTIC_AGENT_HOST_DATA_PATH` -- Defaults to `/opt/drastic-agent/data`. Persistent agent identity, private key, secret, agent SSH identity, SSH `known_hosts`, synchronized configuration, operation state, and agent-encrypted restic access keys. Plaintext restic access keys are kept only in memory.
- `DRASTIC_AGENT_HOST_ROOT_PATH` -- Host path mounted read-only to `/mnt/host` inside the agent.

The default root path is `/`, which allows file jobs to reference host paths via `/mnt/host/...`.

## Native Agent

For Linux hosts with systemd (amd64 or arm64), use the pipe installer:

```bash
curl -fsSL https://backup.example.net/install | bash
```

It installs directly from Git, including a private `uv`, Python 3.11 and virtual environment. The host needs `bash`, `curl`, `git`, systemd and root or sudo access. No system Python installation or package-manager changes are needed. SSH/SFTP jobs additionally require the host's OpenSSH client; Proxmox jobs require their existing host tools.

The script asks for a Git branch, tag or commit through `/dev/tty`, including when piped into Bash. Press Enter for `main` (released versions), or enter `develop` for development. The backend supplies the server URL and `DRASTIC_AGENT_GIT_REPOSITORY`. `--ref` skips the prompt; unattended installations without a terminal default to `main`. Local `--source` installs use the checkout without asking for a ref.

You can also pipe the script directly from Git, without using the backend installer endpoint:

```bash
curl -fsSL https://raw.githubusercontent.com/devmato/drastic-backup/main/scripts/install-drastic-agent.sh | bash -s -- \
  --server https://backup.example.net
```

Select a branch, release tag or commit with `--ref`. For unattended installs, supply registration credentials:

```bash
curl -fsSL https://backup.example.net/install | bash -s -- \
  --ref vX.Y.Z \
  --user admin \
  --password replace-me
```

Use the release tag matching your deployed server, and choose a tag that includes this installer. For a development version, pass `--ref develop` or a commit SHA. Override the source repository with `--repository URL`. Registration credentials are not persisted by the installer; the existing registration flow saves the generated identity and keypair.

To install your current local checkout, including uncommitted changes:

```bash
bash scripts/install-drastic-agent.sh --source "$PWD" \
  --server https://backup.example.net --ref develop
```

The checkout is copied into the managed installation, without local environment files or runtime directories. Future `update` commands fetch the stored repository/ref; use `install --source PATH` again to deploy local changes.

### Layout and Updates

All managed files live under one root:

```text
/opt/drastic-agent/
├── bin/drastic-agent
├── tools/                 # private uv and Python
├── releases/<id>/         # source checkout and virtual environment
├── current -> releases/<id>
├── cache/                 # uv and Restic caches
├── data/                  # identity, keys, operation state and Restic binary
├── drastic-agent.env
└── install.json
```

Outside that root, the installer creates only `/usr/local/bin/drastic-agent` (a symlink) and `/etc/systemd/system/drastic-agent.service`. The service runs as root, starts at boot, and logs to the system journal.

```bash
sudo drastic-agent status
sudo drastic-agent update
sudo drastic-agent update --ref vX.Y.Z
journalctl -u drastic-agent.service
```

Updates follow the saved branch, or keep the selected tag/commit pinned until you change `--ref`. Dependencies are installed with `uv sync --frozen --no-dev --no-editable`. A new source/venv directory is prepared and checked before stopping the running service. If the new service fails its startup checks, the old installation is restored. Agent data and additional environment settings (for example Proxmox credentials) are retained. This rollback covers program files, not changes made by the running agent to its data.

**Agent Properties > Actions > Update** starts the same command for managed native Linux agents using protocol 1 or later. Running/queued operations and duplicate updates are rejected; new executions and schedules wait until the installer finishes, including its startup checks. The UI acknowledges the start and refreshes the version after reconnect. Check completion and errors with `journalctl -u drastic-agent-update.service`.

If the service cannot be stopped during rollback, both releases remain on disk and the installer reports the blocked rollback. Stop `drastic-agent.service` before retrying. An interrupted first installation cleans up its new runtime files; any existing or newly registered agent identity remains available for a retry.

The install page uses `DRASTIC_PUBLIC_URL` for the setup URL when configured, otherwise the browser origin. Native installations report their install type as `git`; Docker installations continue to report `docker`.

### Protocol Compatibility

Agents report `protocol_version` when connecting. The backend checks command minimum versions and the UI hides unsupported features. Missing means legacy protocol 0; protocol 1 adds web-based Proxmox configuration and `update`, protocol 2 adds Proxmox restore support, and protocol 3 adds connection status, connection removal and TrueNAS snapshot backups. Unknown versions are rejected; update the server before its agents.

Existing agents without a protocol number report as protocol 0. Update them once manually with `sudo drastic-agent update` to enable the update button and web-based Proxmox configuration. Docker agents must be updated through their container deployment.

### Uninstall

Uninstall the native agent with:

```bash
sudo drastic-agent uninstall
```

This stops and disables the service, removes the external service and command, and deletes program files, Python, dependencies and caches. `data/`, `drastic-agent.env`, installation metadata and the ownership marker remain for reinstalling with the same identity and settings.

Remove local agent state as well:

```bash
sudo drastic-agent uninstall --purge
```

`--purge` removes the entire managed root. Unattended uninstall requires `--yes`. Backup sources and remote repositories are never removed, and the server-side agent entry is not deleted automatically. Foreign service files, replaced command links and unsafe managed paths are rejected. If the service cannot be stopped, files are retained.

After a non-purge uninstall the command itself is gone. Reinstall with the pipe installer to reuse the saved identity, or run the lifecycle script from a checkout with a system Python (`sudo python3 scripts/drastic-agent-installer.py uninstall --purge`) to remove the retained state.

### Migrating the Previous Native Installer

The new bootstrap detects the old `/opt/drastic-agent/agentctl` installation and stops before modifying it. Save any custom environment settings, run **`sudo drastic-agent uninstall` without `--purge`** using the old installation, then run the new pipe installer with `--reuse-data`. The old uninstaller retains `data/`, which the new installer reuses without registering another identity. Reapply custom settings to `drastic-agent.env` afterward.

```bash
curl -fsSL https://backup.example.net/install | bash -s -- --reuse-data
```

The explicit flag is needed only for state without the new ownership marker. This prevents silently adopting a Docker agent's data directory. Stop and remove the old agent before reusing its identity; do not run two agents from the same data directory.

Native release archives, `/agentctl`, `/agents/<os>/<arch>` and the backend artifact cache are no longer used. Remove old `DRASTIC_AGENT_RELEASE_*`, `DRASTIC_AGENT_ARTIFACT_*` and `DRASTIC_ASSET_CACHE_PATH` settings. An existing artifact-cache directory can be removed separately once it is no longer used by an older deployment. Docker image builds and Docker/Compose installation remain supported.

## Registration

The agent registers with a dRastic username and password. In production, configure these values explicitly:

- `DRASTIC_SERVER`
- `DRASTIC_USER`
- `DRASTIC_PASSWORD`

After registration, the agent stores its generated credentials and keypair in its data directory and reuses them on restart.

Protect the agent data directory as sensitive local state. Native and Docker installs set the data directory to `0700` and the local `config.ini` to `0600` where supported. If this directory is copied, the copy can authenticate as the same agent until that agent is removed or rotated.

When a repository is assigned or used, the server can provision repository access through the agent public key. The encrypted access key is persisted locally and can be decrypted again after an agent restart. This supports offline scheduled jobs only when a custom repository remains directly reachable; native repositories still require the backend restic proxy. Preserve and protect the agent data directory, because losing the local identity or encrypted key requires backend reprovisioning.

For SSH/SFTP repositories, the agent uses its own local SSH identity. Copy the agent SSH public key from the agent properties dialog and install it on the target host. The agent keeps a local `known_hosts` file in its data directory and trusts new hosts on first use. Reset only the affected agent's known hosts from the agent properties **Actions** tab when a target host key changes.

For Homelab deployments, register agents from a trusted network and then remove `DRASTIC_USER` and `DRASTIC_PASSWORD` from long-lived agent environment files when they are no longer needed for first-time registration.

## Proxmox Backups

For Proxmox jobs, open **Agents > your agent > Connections > Proxmox** while the agent is online. Enter the token ID (`user@realm!tokenname`) and token secret, then use **Test connection** and **Save**. The **Configure Proxmox** button in the backup job form opens the agent page in another tab, preserving the job draft. Protocol 3 agents expose Proxmox jobs when the connection is configured and local `vzdump` is available. Removing the connection also disables the environment fallback until credentials are saved again.

The test checks local `vzdump` availability and API access to supported VMs without saving changes. Saved settings apply to guest discovery and all Proxmox jobs on that agent immediately, without a restart. The secret is encrypted for the agent before command dispatch and stored encrypted in its local database; it is never returned to the web UI. Leaving the secret field empty preserves the current token. Changing the API URL or token ID requires entering a token secret again.

Under **Advanced**, the API URL defaults to `https://127.0.0.1:8006/api2/json`, the local node is detected automatically, and TLS verification can be enabled. Disable verification when using a self-signed certificate without a trusted CA.

Existing agent environment configuration remains supported until settings are saved through the web UI:

- `DRASTIC_PROXMOX_API_URL`
- `DRASTIC_PROXMOX_TOKEN_ID`
- `DRASTIC_PROXMOX_TOKEN_SECRET`
- `DRASTIC_PROXMOX_NODE`
- `DRASTIC_PROXMOX_VERIFY_TLS`

The agent must run directly on the Proxmox node with the required tooling for `vzdump` based backups. A remote API URL does not enable remote backup execution. Update older agents to use the web UI configuration commands.
