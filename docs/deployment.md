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

The production stack uses published images from `DRASTIC_SERVER_IMAGE` and `DRASTIC_AGENT_IMAGE`. Official GHCR images are published for `linux/amd64` and `linux/arm64`. Docker-based agent snippets use `DRASTIC_AGENT_IMAGE` directly, so target hosts must be able to pull that image from the configured registry. Set both images and `DRASTIC_AGENT_ARTIFACT_TAG` to the same explicit release tag, such as `v0.1.0`; do not mix releases or deploy `latest`. The integrated rest-server is independently pinned with `DRASTIC_REST_SERVER_VERSION` so upgrades are intentional.

The bootstrap admin seed creates the configured user when it does not exist. It never changes an existing user's password or recovery secrets.

Native agent artifacts are loaded from public GitHub, Forgejo, or Gitea releases. GitHub releases provide Linux amd64 and arm64 artifacts. The backend caches each requested artifact under `DRASTIC_ASSET_CACHE_PATH` on first access and serves subsequent requests from that local cache.

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

The backend host port binds to `127.0.0.1` by default. Keep `DRASTIC_HOST_BACKEND_ADDRESS=127.0.0.1` when the reverse proxy runs on the same host. Set it to a specific private address only when a proxy on another host must connect; avoid `0.0.0.0` unless host firewalling provides the intended restriction.

The proxy must preserve the original `Host`, set `X-Forwarded-Proto` to the client scheme, and support WebSocket upgrades (`Upgrade` and `Connection` headers) on the same routes as normal HTTP traffic. Use proxy timeouts long enough for backup and restore requests. Incorrect forwarded scheme handling can produce HTTP URLs or break secure-cookie authentication.

For example, an Nginx proxy location needs at least:

```nginx
location / {
    proxy_pass http://127.0.0.1:5050;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 300s;
}
```

The HTTPS production examples and generator set `DRASTIC_JWT_COOKIE_SECURE=true`, so browsers only send login cookies over HTTPS. Set it to `false` only for an intentionally HTTP-only trusted deployment, and use an `http://` public URL in that case.

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

The current pre-production release uses a consolidated `init` Alembic baseline for a fresh database. No upgrade migration from older development schemas is provided because no production deployment required compatibility when the baseline was replaced. Recreate old development or test databases; do not point this baseline at an older schema and assume an in-place upgrade is supported.
