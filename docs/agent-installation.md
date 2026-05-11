# Agent Installation

Agents execute backups and restores. They authenticate against the server with a generated agent identifier and secret after registration.

Agents are high-trust components. A native agent usually runs as `root`, and a Docker agent with Docker socket access can effectively control the Docker host. Install agents only on hosts you trust with backup and restore access.

## Docker Agent

Use the install snippets in the web UI under **Agents > Add Agent**. Docker snippets use `DRASTIC_AGENT_IMAGE` directly and do not require a project checkout on the target host.

If you still run the standalone Compose file, create an environment file from the production example and configure at least the server URL, login credentials, and agent image:

```bash
cp .env.prod.example .env.prod
```

Start the agent with Docker Compose:

```bash
docker compose -f docker-compose.agent.yaml --env-file .env.prod up -d
```

Important agent paths:

- `DRASTIC_AGENT_HOST_DATA_PATH` -- Persistent agent identity and keypair. Repository keys are hydrated into memory after backend sync and are not stored here.
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

The installer downloads the matching Linux artifact from the backend cache, registers the agent once, writes a systemd service, and starts it. Registration credentials are not written to disk; the agent stores its generated identifier, secret, and keypair in its data directory.

The install page uses `DRASTIC_PUBLIC_URL` for displayed setup and artifact URLs when it is configured. If it is empty, the browser origin is used instead.

Native Linux paths:

```text
/usr/local/bin/drastic-agent
/opt/drastic-agent/bin/drastic-agent
/opt/drastic-agent/data/
/opt/drastic-agent/drastic-agent.env
/etc/systemd/system/drastic-agent.service
```

Uninstall the native agent with:

```bash
sudo drastic-agent uninstall
```

Remove local agent state as well:

```bash
sudo drastic-agent uninstall --purge
```

For manual installs and unsupported native service targets, the backend downloads the configured public GitHub, Forgejo, or Gitea release asset once, stores it under `DRASTIC_ASSET_CACHE_PATH`, and serves cached files from:

```text
/agents/<os>/<arch>
```

Artifact names are built from `DRASTIC_AGENT_ARTIFACT_NAME_TEMPLATE`, for example:

```text
drastic-agent-linux-amd64-v0.1.0.tar.gz
drastic-agent-windows-amd64-v0.1.0.zip
```

Supported artifact target values currently include `linux/amd64`.

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

When a repository is assigned or used, the server can provision repository access through the agent public key. After startup sync, the agent keeps local restic repository keys in memory so scheduled jobs can run without an active browser login. If the agent process restarts, backend sync is required again before repository jobs can access their repositories.

For Homelab deployments, register agents from a trusted network and then remove `DRASTIC_USER` and `DRASTIC_PASSWORD` from long-lived agent environment files when they are no longer needed for first-time registration.

## Proxmox Backups

For Proxmox jobs, configure API credentials on the agent:

- `DRASTIC_PROXMOX_API_URL`
- `DRASTIC_PROXMOX_TOKEN_ID`
- `DRASTIC_PROXMOX_TOKEN_SECRET`
- `DRASTIC_PROXMOX_NODE`
- `DRASTIC_PROXMOX_VERIFY_TLS`

The agent host must have the required Proxmox tooling available for `vzdump` based backups.
