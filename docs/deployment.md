# Deployment

dRastic Backup is deployed as two Compose stacks:

- Server stack: backend, web UI, database, and integrated restic REST server.
- Agent stack: one agent per host that should execute backups.

## Server Stack

Create a production environment file:

```bash
./scripts/generate-env.sh prod
```

Alternatively copy `.env.prod.example` to `.env.prod` manually. Edit `.env.prod` and set production values for secrets, database credentials, public URL, storage paths, image names, and release artifact settings. Back it up with the database and repository storage.

Start the server stack:

```bash
docker compose -f docker-compose.yaml --env-file .env.prod up -d
```

The web UI is served by the backend. `DRASTIC_PUBLIC_URL` is optional; if it is empty, the install page derives displayed URLs from the browser's current origin.

The production stack uses published images from `DRASTIC_SERVER_IMAGE` and `DRASTIC_AGENT_IMAGE`. Docker-based agent snippets use `DRASTIC_AGENT_IMAGE` directly, so target hosts must be able to pull that image from the configured registry. For releases, prefer matching version tags such as `v0.1.0` over `latest`.

The bootstrap admin seed creates the configured user when it does not exist. It does not reset an existing user's password unless `DRASTIC_BOOTSTRAP_ADMIN_UPDATE=true` is set intentionally.

Native agent artifacts are loaded from public GitHub, Forgejo, or Gitea releases. The backend caches each requested artifact under `DRASTIC_ASSET_CACHE_PATH` on first access and serves subsequent requests from that local cache.

## Agent Installation

For Linux hosts with systemd, use the public installer from the web UI or directly from the backend:

```bash
curl -fsSL https://backup.example.net/install | bash
```

The native installer stores agent files under `/opt/drastic-agent`, installs a wrapper at `/usr/local/bin/drastic-agent`, and creates `drastic-agent.service`.

## Agent Stack

For Docker-based agents, use the same `.env.prod` style configuration or a host-specific environment file:

```bash
docker compose -f docker-compose.agent.yaml --env-file .env.prod up -d
```

The agent talks to the server through `DRASTIC_SERVER`.

## Runtime Data

The server stack uses host paths for persistent data:

- `DRASTIC_HOST_DB_PATH` -- MariaDB data.
- `DRASTIC_REST_SERVER_STORAGE_PATH` -- Native restic repositories.
- `DRASTIC_ASSET_CACHE_PATH` -- Cached release assets served by the backend.

The agent stack uses host paths for agent state and backup source access:

- `DRASTIC_AGENT_HOST_DATA_PATH` -- Agent registration and runtime state.
- `DRASTIC_AGENT_HOST_ROOT_PATH` -- Read-only source path mounted to `/mnt/host`.

## Reverse Proxy

Place dRastic behind HTTPS in production. Set `DRASTIC_PUBLIC_URL` when the backend should generate a canonical public URL instead of deriving it from each request:

```text
DRASTIC_PUBLIC_URL=https://backup.example.net
```

The public URL is used for generated repository URLs and agent registration responses. The install page uses it when present and otherwise falls back to `window.location.origin`.

The production examples default `DRASTIC_JWT_COOKIE_SECURE=false` so installations that are only reachable through a VPN can still use HTTP. When serving dRastic through HTTPS, set `DRASTIC_JWT_COOKIE_SECURE=true` so browsers only send login cookies over HTTPS.

## Homelab Exposure

dRastic Backup is intended for trusted Homelab environments. Prefer running it behind HTTPS and either a VPN, private network, or an authenticated reverse proxy. Complete the initial setup before exposing the service beyond the local host or trusted network.

## Updating

Pull the new images and recreate the Compose stack:

```bash
docker compose -f docker-compose.yaml --env-file .env.prod pull
docker compose -f docker-compose.yaml --env-file .env.prod up -d
docker compose -f docker-compose.agent.yaml --env-file .env.prod pull
docker compose -f docker-compose.agent.yaml --env-file .env.prod up -d
```

Database migrations run during backend startup.
