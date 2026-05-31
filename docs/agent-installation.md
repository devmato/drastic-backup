# Agent Installation

Agents execute backups and restores. They authenticate against the server with a generated agent identifier and secret after registration.

Agents are high-trust components. A native agent usually runs as `root`, and a Docker agent with Docker socket access can effectively control the Docker host. Install agents only on hosts you trust with backup and restore access.

## Docker Agent

Use the install snippets in the web UI under **Agents > Add Agent**. Docker snippets use `DRASTIC_AGENT_IMAGE` directly and do not require a project checkout on the target host. Official GHCR agent images support `linux/amd64` and `linux/arm64`.

If you still run the standalone Compose file, create an environment file from the production example and configure at least the server URL, login credentials, and agent image:

```bash
cp .env.prod.example .env.prod
```

Start the agent with Docker Compose:

```bash
docker compose -f docker-compose.agent.yaml --env-file .env.prod up -d
```

Important agent paths:

- `DRASTIC_AGENT_HOST_DATA_PATH` -- Persistent agent identity, secret, agent SSH identity, and SSH `known_hosts` data. Restic access keys are hydrated into memory after backend sync and are not stored here.
- `DRASTIC_AGENT_HOST_ROOT_PATH` -- Host path mounted read-only to `/mnt/host` inside the agent.

The default root path is `/`, which allows file jobs to reference host paths via `/mnt/host/...`.

## Native Agent

For Linux hosts with systemd, install the native agent with the public setup script:

```bash
curl -fsSL https://backup.example.net/install | bash
```

For unattended installs, pass registration credentials as arguments:

```bash
curl -fsSL https://backup.example.net/install | bash -s -- --user admin --password replace-me
```

The public `/install` script bootstraps the local lifecycle manager at `/opt/drastic-agent/agentctl` and delegates installation to it. The default action is `auto`: install when no native agent exists, otherwise prompt to update, uninstall, or cancel. In non-interactive environments, pass `--action install`, `--action update`, or `--action uninstall` explicitly.

The default native install source is `release` with `latest` version resolution. In this mode, `latest` means the artifact matching the running backend version, not necessarily the newest upstream release:

```bash
curl -fsSL https://backup.example.net/install | bash -s -- \
  --action install \
  --source release \
  --version latest \
  --user admin \
  --password replace-me
```

Install a concrete release artifact with:

```bash
curl -fsSL https://backup.example.net/install | bash -s -- \
  --action install \
  --source release \
  --version v0.1.0 \
  --user admin \
  --password replace-me
```

For staging or pre-release tests, install the native agent from a git commit without creating a release artifact:

```bash
curl -fsSL https://backup.example.net/install | bash -s -- \
  --action install \
  --source git \
  --version <commit-sha> \
  --user admin \
  --password replace-me
```

Git installs require `git`, `uv`, and Python on the target host. Use a commit SHA for reproducible tests. `--source git --version latest` is allowed for convenience, but installs the repository default branch and is not reproducible. Override the repository with `--git-repo https://github.com/devmato/drastic-backup.git` when needed.

Agents report their install type as `docker`, `release`, `git`, or `manual`. Docker and manual agents are not managed by `agentctl`; native release and git agents can be updated or uninstalled through the local lifecycle manager.

Update an installed native agent locally with:

```bash
sudo drastic-agent update --source release --version latest
```

Pre-release git updates use the same local lifecycle manager:

```bash
sudo drastic-agent update --source git --version <commit-sha>
```

The installer detects the host CPU architecture, downloads the matching Linux artifact from the backend cache, registers the agent once, writes a systemd service, and starts it. Registration credentials are not written to disk; the agent stores its generated identifier, secret, and keypair in its data directory.

The install page uses `DRASTIC_PUBLIC_URL` for displayed setup and artifact URLs when it is configured. If it is empty, the browser origin is used instead.

Native Linux paths:

```text
/usr/local/bin/drastic-agent
/opt/drastic-agent/agentctl
/opt/drastic-agent/bin/drastic-agent
/opt/drastic-agent/source/
/opt/drastic-agent/data/
/opt/drastic-agent/drastic-agent.env
/etc/systemd/system/drastic-agent.service
```

Uninstall the native agent with:

```bash
sudo drastic-agent uninstall
```

This delegates to `/opt/drastic-agent/agentctl --action uninstall`, so local uninstalls do not need to fetch `/install` again.

Remove local agent state as well:

```bash
sudo drastic-agent uninstall --purge
```

For manual installs and unsupported native service targets, the backend downloads the configured public GitHub, Forgejo, or Gitea release asset once, stores it under `DRASTIC_ASSET_CACHE_PATH`, and serves cached files from:

```text
/agents/<os>/<arch>
```

A concrete release tag can be requested with:

```text
/agents/<os>/<arch>?version=v0.1.0
```

Artifact names are built from `DRASTIC_AGENT_ARTIFACT_NAME_TEMPLATE`, for example:

```text
drastic-agent-linux-amd64-v0.1.0.tar.gz
drastic-agent-linux-arm64-v0.1.0.tar.gz
drastic-agent-windows-amd64-v0.1.0.zip
```

Supported Linux artifact target values currently include `linux/amd64` and `linux/arm64`.

Release asset URLs are built without provider-specific API calls:

```text
<DRASTIC_AGENT_RELEASE_BASE_URL>/<DRASTIC_AGENT_RELEASE_REPOSITORY>/releases/download/<tag>/<asset-name>
```

If `DRASTIC_AGENT_ARTIFACT_TAG` is empty, the backend uses `v<backend-version>`, for example `v0.1.0`.

## Registration

The agent registers with a dRastic username and password. In production, configure these values explicitly:

- `DRASTIC_SERVER`
- `DRASTIC_USER`
- `DRASTIC_PASSWORD`

After registration, the agent stores its generated credentials and keypair in its data directory and reuses them on restart.

Protect the agent data directory as sensitive local state. Native and Docker installs set the data directory to `0700` and the local `config.ini` to `0600` where supported. If this directory is copied, the copy can authenticate as the same agent until that agent is removed or rotated.

When a repository is assigned or used, the server can provision repository access through the agent public key. After startup sync, the agent keeps local restic access keys in memory so scheduled jobs can run without an active browser login. If the agent process restarts, backend sync is required again before repository jobs can access their repositories.

For SSH/SFTP repositories, the agent uses its own local SSH identity. Copy the agent SSH public key from the agent properties dialog and install it on the target host. The agent keeps a local `known_hosts` file in its data directory and trusts new hosts on first use. Reset only the affected agent's known hosts from the agent properties **Actions** tab when a target host key changes.

For Homelab deployments, register agents from a trusted network and then remove `DRASTIC_USER` and `DRASTIC_PASSWORD` from long-lived agent environment files when they are no longer needed for first-time registration.

## Proxmox Backups

For Proxmox jobs, configure API credentials on the agent:

- `DRASTIC_PROXMOX_API_URL`
- `DRASTIC_PROXMOX_TOKEN_ID`
- `DRASTIC_PROXMOX_TOKEN_SECRET`
- `DRASTIC_PROXMOX_NODE`
- `DRASTIC_PROXMOX_VERIFY_TLS`

The agent host must have the required Proxmox tooling available for `vzdump` based backups.
