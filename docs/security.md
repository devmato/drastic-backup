# Security Model

dRastic Backup is designed for trusted Homelab use, not as a multi-tenant enterprise backup platform.

## Trust Assumptions

- The admin account is fully trusted.
- Registered agents are fully trusted.
- Anyone who can configure jobs or actions can make agents read backup paths and execute configured actions.
- A Docker agent with `/var/run/docker.sock` access can effectively control the Docker host.
- A native agent usually runs as `root` so it can read files and perform restores.

## Recommended Deployment

- Use HTTPS through a reverse proxy.
- Keep the UI/API on a private network, VPN, or trusted Homelab segment.
- Complete the first admin setup before exposing the service beyond localhost or the trusted network.
- Use versioned server and agent image tags for release deployments.
- Back up `.env.prod` or your secret store together with the database and repository storage.
- The examples keep `DRASTIC_JWT_COOKIE_SECURE=false` for HTTP-only VPN deployments. Set it to `true` when serving the UI/API through HTTPS.

## Secrets

`DRASTIC_APP_MASTER_SECRET` derives backend runtime and settings-encryption keys. Repository passwords are encrypted with a per-user recovery key that is decrypted at login and kept only in browser memory, then provisioned to agents through public-key envelopes when they need repository access. Revealing a repository password in the UI requires re-entering the account password.

Agents generate a keypair during registration and advertise the public key to the server. The backend stores agent-specific envelopes for local restic repository keys and recovery access, but cannot decrypt them by itself.

On startup sync, an agent decrypts its local restic repository keys into memory and uses them as the primary access path. The keys are not persisted in the agent data directory. If a local key is missing or no longer works, the agent requests the recovery envelope from the backend, decrypts it in memory, initializes or re-provisions repository access, and discards the recovery password after use.

This reduces the risk of copied agent state, but the state still authenticates the agent. A copied agent identity can request fresh envelopes until the agent is removed or rotated, and a running agent still holds repository keys in memory.

Agent registration currently uses a dRastic username and password. After registration, agents store their generated identifier and secret and use those credentials for ongoing server and repository access.

## Backup Safety

- Test restores regularly.
- Restore to a temporary path before overwriting production data.
- Keep retention policies conservative until restore validation is routine.
