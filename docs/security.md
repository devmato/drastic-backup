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
- If pinning a version for reproducible deployment or rollback, use the same release tag for server and agent images and the native agent Git ref. Docker Compose defaults to the latest published release images.
- Back up `.env` or your secret store together with the database and repository storage.
- Keep the default backend publication on `127.0.0.1` and expose it through a correctly configured HTTPS reverse proxy.
- Production HTTPS examples set `DRASTIC_JWT_COOKIE_SECURE=true`. Disable it only for an intentionally HTTP-only trusted deployment.

## Secrets

`DRASTIC_APP_MASTER_SECRET` derives backend runtime and settings-encryption keys. Repository passwords are encrypted with a per-user recovery key that is decrypted at login and kept only in browser memory, then provisioned to agents through public-key envelopes when they need repository access. Revealing a repository password in the UI requires re-entering the account password. Repository password rotation after creation is not supported yet.

Custom repository environment values and notification URLs use the backend settings key, not user recovery encryption. Database plus master secret can decrypt those fields, including any credentials explicitly stored there. Do not treat them as end-to-end encrypted against the server.

Each agent generates and stores its own SSH identity locally. The backend stores only the agent SSH public key, fingerprint, and algorithm so users can copy the public key into SSH/SFTP targets. Treat every agent assigned to an SSH repository as trusted with that repository's SSH access.

Agents generate a keypair during registration and advertise the public key to the server. The backend stores agent-specific envelopes for local restic access keys and recovery access, but cannot decrypt them by itself.

- Agent data stores **encrypted** Restic access-key envelopes and the private key needed to decrypt them. Plaintext Restic passwords remain in memory.
- A synchronized agent can unlock its keys after restart without the backend. Offline backups require a directly reachable custom repository; native repositories still need the backend proxy.
- Missing or invalid access keys require recovery provisioning. Preserve the local private key and encrypted envelopes together.

For SSH repositories, agents use their local SSH identity for restic processes. SSH `known_hosts` data is local agent state and can be reset per agent from the agent properties actions. Rotating an agent SSH key requires installing the new public key on affected SSH/SFTP targets.

**A copy of the complete agent data directory can unlock already synchronized repository keys and authenticate as that agent.** Removing the server-side agent blocks further server/proxy access; it does not revoke credentials already copied for directly reachable custom repositories or SSH targets. Repository password rotation is not available in the UI. Treat suspected compromise as a storage-credential incident, not just an agent-registration issue. Native and Docker installs use `0700` for agent data and `0600` for `config.ini` where supported.

Agent registration currently uses a dRastic username and password. After registration, agents store their generated identifier and secret and use those credentials for ongoing server and repository access.

Changing an account password rewraps its recovery key and revokes browser sessions and debug access. It does not revoke registered agents or rotate repository/SSH credentials.

Account passwords are limited to 72 UTF-8 bytes by bcrypt; non-ASCII characters can use multiple bytes. Longer passwords are rejected, never truncated. This applies to web setup, password changes, CLI creation and bootstrap credentials.

Revoking a browser session also disconnects its live notification sockets; password changes disconnect every session's sockets. When an authenticated agent replaces its encryption public key, its old server-held envelopes are invalidated. Synchronize with the unlocked account recovery key to provision envelopes for the new key.

## Backup Safety

- Test restores regularly.
- Restore to a temporary path before overwriting production data.
- Keep retention policies conservative until restore validation is routine.
