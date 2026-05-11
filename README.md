<p align="center">
  <img src="apps/frontend/public/icons/drastic-backup-icon.svg" alt="dRastic Backup" width="96" height="96">
</p>

<h1 align="center">dRastic Backup</h1>

<p align="center">
  Self-hosted restic backups with a Flask backend, Quasar web UI, and lightweight agents.
</p>

<p align="center">
  <a href="LICENSE.md"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green.svg"></a>
  <img alt="CI: GitHub Actions" src="https://img.shields.io/badge/CI-GitHub%20Actions-blue.svg">
  <img alt="Images: GHCR" src="https://img.shields.io/badge/images-GHCR-blue.svg">
</p>

dRastic Backup is a restic-based backup system with a Flask backend, Quasar web UI, and agents that execute backups on target hosts.

It is designed for trusted self-hosted and homelab environments where backup jobs run close to the systems they protect.

## Disclaimer

dRastic Backup is developed with significant AI assistance. Large parts of the codebase have not been independently reviewed.

Use this software at your own risk. There is no warranty or guarantee that it will work correctly, protect your data, or avoid data loss, security issues, misconfiguration, downtime, or other problems caused directly or indirectly by using the application.

Always test backups and restores carefully before relying on this software for important data.

## Features

- Managed and custom restic repositories.
- Remote agents for host-local backup execution.
- Scheduled backup jobs with retention policies.
- File restore workflows through the web UI.
- Notifications, reports, and agent install helpers.

## Quick Start

Start the development stack:

```bash
./scripts/dev.sh up
```

Create local environment files when needed:

```bash
./scripts/generate-env.sh dev
./scripts/generate-env.sh test
./scripts/generate-env.sh prod
```

Open the frontend at `http://127.0.0.1:9050` and sign in with the development bootstrap account from `.env.dev`.

For the full local workflow, see [Development Setup](docs/development.md).

## Documentation

Full documentation starts at [`docs/index.md`](docs/index.md) and can be served as a MkDocs site.

- [Documentation Index](docs/index.md)
- [User Guide](docs/user-guide.md)
- [Agent Installation](docs/agent-installation.md)
- [Backup and Restore](docs/backup-and-restore.md)
- [Deployment](docs/deployment.md)
- [Configuration](docs/configuration.md)
- [Security](docs/security.md)
- [Development Setup](docs/development.md)
- [CI and Releases](docs/ci-and-releases.md)

Preview the documentation locally:

```bash
python -m pip install mkdocs-material
mkdocs serve
```

## Distribution

The public upstream repository is expected at `https://github.com/devmato/drastic-backup`.

Default container images are published to GitHub Container Registry:

- `ghcr.io/devmato/drastic-backup-server`
- `ghcr.io/devmato/drastic-backup-agent`

## Project Structure

```text
drastic-backup/
├── apps/
│   ├── backend/    # Python / Flask API
│   ├── frontend/   # Vue / Quasar web UI
│   └── agent/      # dRastic agent runtime
├── docs/           # User, deployment, and development documentation
├── libs/
│   └── python/common
└── scripts/
```

See [Project Structure](docs/project-structure.md) for details.

## Contributing

- Contributor guide: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- CI and release guide: [`docs/ci-and-releases.md`](docs/ci-and-releases.md)
- AI agent operating guide: [`AGENTS.md`](AGENTS.md)

## License

dRastic Backup is licensed under the [MIT License](LICENSE.md).
