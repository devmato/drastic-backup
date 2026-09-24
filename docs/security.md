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
- Use the same explicit release tag for server images, agent images, and native agent Git refs; never mix release tags or use `latest` images in production.
- Back up `.env` or your secret store together with the database and repository storage.
- Keep the default backend publication on `127.0.0.1` and expose it through a correctly configured HTTPS reverse proxy.
- Production HTTPS examples set `DRASTIC_JWT_COOKIE_SECURE=true`. Disable it only for an intentionally HTTP-only trusted deployment.

## Secrets

`DRASTIC_APP_MASTER_SECRET` derives backend runtime and settings-encryption keys. Repository passwords are encrypted with a per-user recovery key that is decrypted at login and kept only in browser memory, then provisioned to agents through public-key envelopes when they need repository access. Revealing a repository password in the UI requires re-entering the account password. Repository password rotation after creation is not supported yet.

Each agent generates and stores its own SSH identity locally. The backend stores only the agent SSH public key, fingerprint, and algorithm so users can copy the public key into SSH/SFTP targets. Treat every agent assigned to an SSH repository as trusted with that repository's SSH access.

Agents generate a keypair during registration and advertise the public key to the server. The backend stores agent-specific envelopes for local restic access keys and recovery access, but cannot decrypt them by itself.

On startup sync, an agent decrypts its local restic access keys into memory and uses them as the primary access path. The keys are not persisted in the agent data directory. If a local key is missing or no longer works, the agent requests the recovery envelope from the backend, decrypts it in memory, initializes or re-provisions repository access, and discards the recovery password after use.

For SSH repositories, agents use their local SSH identity for restic processes. SSH `known_hosts` data is local agent state and can be reset per agent from the agent properties actions. Rotating an agent SSH key requires installing the new public key on affected SSH/SFTP targets.

This reduces the risk of copied agent state, but the state still authenticates the agent. A copied agent identity can request fresh envelopes until the agent is removed or rotated, and a running agent still holds restic access keys in memory. Native and Docker installs set the agent data directory to `0700` and the local `config.ini` to `0600` where the host filesystem supports those permissions.

Agent registration currently uses a dRastic username and password. After registration, agents store their generated identifier and secret and use those credentials for ongoing server and repository access.

Changing a user password re-encrypts that user's recovery key with the new password and revokes existing sessions. It does not rotate repository passwords or SSH keys.

## Backup Safety

- Test restores regularly.
- Restore to a temporary path before overwriting production data.
- Keep retention policies conservative until restore validation is routine.
