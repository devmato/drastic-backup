# Development Setup

This page describes local development workflows. For production operation, use the [Deployment](deployment.md) guide.

Before changing application structure, read [Architecture](architecture.md) and [Project Structure](project-structure.md).

## Prerequisites

- Docker with Docker Compose v2.
- Python 3.11 or later.
- `uv` for Python environments.
- Node.js 22 for the frontend.
- Corepack/Yarn for frontend dependencies.
- `mkdocs-material` when previewing documentation locally without Docker.

## Development Stack

Start the default development stack from the repository root:

```bash
./scripts/dev.sh up
```

The default stack starts:

- `db` -- MariaDB.
- `backend` -- Flask API and built docs serving.
- `rest-server` -- Integrated restic REST endpoint for native repositories.
- `frontend` -- Quasar dev server.
- Docker agent -- Agent container for local end-to-end testing.

Open the frontend at the configured development frontend port. The default is `http://127.0.0.1:9050`.

Development credentials are seeded from `.env.dev` by default. The default local account is `admin` / `admin` unless changed in the environment file.

Create optional local overrides with:

```bash
./scripts/generate-env.sh dev
```

This writes `.env.dev.override`, which is loaded automatically by `./scripts/dev.sh` and ignored by Git.

## Run Without Docker Agent

Start the server and frontend stack without the Docker agent:

```bash
./scripts/dev.sh up --no-agent
```

Use this when the agent should run directly on the host or when testing Proxmox-related behavior.

## Run Agent Locally

Start an agent on the host against the Docker development backend:

```bash
./scripts/dev.sh agent-local
```

Run an agent with production-like environment files and an explicit state directory:

```bash
./scripts/dev.sh agent-local --env prod --data-dir /var/lib/drastic-agent
```

Override the server URL explicitly:

```bash
./scripts/dev.sh agent-local --env prod --server https://backup.example.net --data-dir /var/lib/drastic-agent
```

`agent-local` loads `.env.dev` and optional `.env.dev.override` for `dev`. For `test` and `prod` it loads `.env`. An explicit `--env-file` is loaded last.

## Test Native Installer From Git

For native installer testing, start the development stack without the Docker agent. A clean backend checkout provides its exact commit as the native installation target:

```bash
./scripts/dev.sh up --no-agent
```

```bash
curl -fsSL http://127.0.0.1:5050/install | bash -s -- \
  --user admin \
  --password admin
```

The target host needs `git`, `curl`, root or sudo access, and systemd (Linux) or launchd (macOS). Python and `uv` are installed privately under `/opt/drastic-agent`. The backend commit must be pushed to the configured agent repository. Dirty or unknown backend builds have no automatic installation target; for uncommitted changes use `bash scripts/install-drastic-agent.sh --source "$PWD" --server http://127.0.0.1:5050`. Manage the installed agent locally:

```bash
sudo drastic-agent update
```

```bash
sudo drastic-agent uninstall
```

If the installer runs from another host or VM, set `DRASTIC_PUBLIC_URL` to a URL reachable by that host before rendering `/install`.

`--ref <branch|tag|commit>` explicitly overrides one installation/update; subsequent updates use the backend again. Docker builds stamp both product version and full revision. When supplying `DRASTIC_VERSION` manually (for example from a linked Git worktree), also pass `DRASTIC_REVISION=<full-commit-sha>`.

### macOS Acceptance Check

Linux-side tests simulate launchctl for install/update/rollback/removal and exercise the unchanged shared file-backup/restore code. They cannot establish macOS TCC permission behavior or filesystem fidelity. Follow [macOS installation and permissions](agent-installation.md#macos-permissions-and-first-backup) on a real Mac:

1. Install from the backend-matched build, grant access and create a file job using a small protected test folder. Include spaces/Unicode names, a symlink, an excluded file and a file with an extended attribute.
2. Run the backup **through the web UI**, restore to a separate physical directory and compare content, exclusions, modes, symlink targets and extended attributes.
3. Run another backup after changing a file; verify the matching parent and restore the new version. Exercise a scheduled run and retention with a disposable repository.
4. With permission removed, verify the report does not claim a complete successful backup. Restore permission, restart the daemon and repeat successfully.
5. Restart the Mac, unlock FileVault if enabled, check reconnection, then test waking from sleep.
6. Update via CLI and the web action; verify identity, configuration and permissions, then run another backup/restore. Test startup rollback only with a deliberately failing disposable test build.
7. Uninstall without purge and reinstall; the identity must survive. Check purge separately using only disposable agent state.

Run the acceptance check on both Intel and Apple Silicon before claiming both are verified. macOS system logs and the agent/update logs named in the installation guide are the relevant evidence when a check fails.

## Reset Local State

Reset persistent development volumes without removing local images:

```bash
./scripts/dev.sh reset-storage
```

`reset-storage` and `clean` also remove local `apps/agent/data` runtime state so Docker and host-based agent runs reset consistently.

**Destructive:** these commands remove development databases, local repository contents and agent identities. Stop any host-run development agent first; never use these paths for production backups.

## Backend Checks

Run backend tests:

```bash
cd apps/backend
uv sync --frozen --dev
uv run pytest
```

The current Alembic history is a new `init` baseline for fresh databases. It intentionally does not provide an upgrade path from earlier development schemas because there were no production deployments to preserve when the baseline was replaced. Recreate an old development or test database instead of stamping or upgrading it to this baseline.

Verify the baseline against a disposable MariaDB container:

```bash
cd apps/backend
./scripts/test-migrations.sh
```

The script checks migration history, upgrades/checks a fresh MariaDB database, downgrades to `base`, repeats the upgrade/check, and runs the MariaDB report-deletion concurrency regression. Docker must be available; this is not a validation of arbitrary historical database upgrades.

Run lint checks for modified backend files:

```bash
cd apps/backend
uv run ruff check src/drastic_server/app.py
```

## Agent Checks

Run agent tests:

```bash
cd apps/agent
uv sync --frozen --dev
uv run pytest
```

## Common Library Checks

Run common library tests. Release workflow regression tests also require Bash, Git and `jq` (available on the GitHub Ubuntu runners); GitHub and Docker calls are simulated:

```bash
cd libs/python/common
uv sync --frozen --dev
uv run pytest
```

## Frontend Checks

Install and build the frontend:

```bash
cd apps/frontend
corepack enable
yarn install --immutable
yarn lint
yarn test
yarn build
```

## Documentation Checks

Build the documentation site from the repository root:

```bash
mkdocs build --strict --site-dir docs-site
```

The development Compose stack builds the docs into `/tmp/drastic-docs-site` inside the backend container and serves them at `/docs/`.

## Live Proxmox Checks

Use a disposable test host, root privileges and the [restore dependencies](backup-and-restore.md#proxmox-vm-and-guest-file-restore). Normal tests mock host commands; these checks need actual Restic/FUSE or Proxmox resources.

Restic/FUSE disk checks need Linux, `/dev/fuse`, `fusermount3`, Restic and system `python3-fuse`, but not Proxmox or guestfs:

```bash
cd apps/agent
uv run pytest tests/test_guest_disks.py tests/test_guest_linux.py -v
```

Guest-file and optional whole-VM round trip, from `apps/agent`:

```bash
# A small existing TEST VM on LVM-thin; the test creates/deletes its temporary snapshot.
export DRASTIC_TEST_PROXMOX_VMID=990000
export DRASTIC_TEST_GUEST_VOLUME=/dev/vg/root
export DRASTIC_TEST_GUEST_FILE=/etc/hostname
export DRASTIC_TEST_GUEST_SHA256='replace-with-original-file-sha256'
# These authorize creating a stopped VM; the destination VMID must be FREE cluster-wide.
export DRASTIC_TEST_RESTORE_VMID=990001
export DRASTIC_TEST_RESTORE_STORAGE=local-lvm
uv run pytest tests/test_proxmox_restore_integration.py -v
```

- Set the source volume/path/checksum from your test guest. Repeat with a Windows/NTFS guest and a known file to check that platform.
- The imported VM remains stopped for manual boot verification and removal. Omitting the two restore-target settings skips whole-VM import.
- Use a dedicated disposable `--basetemp` directory if necessary; pytest may clear its contents.

Native/CBT round trip, also from `apps/agent`:

```bash
# Requires root, local-lvm, Restic and native FUSE/libnbd tools.
# VMIDs 990103 and 990104 must be FREE; only the newly created test VMs are removed.
DRASTIC_TEST_NATIVE_ROUNDTRIP=1 uv run pytest \
  tests/test_proxmox_native.py::test_real_native_proxmox_roundtrip \
  --basetemp=/var/tmp/drastic-native-test -s
```

This exercises full/CBT reads, upload/crash recovery, checkpoint invalidation, cleanup, pruning and streaming import with EFI/TPM. A real restored-guest boot/application check is still required.
