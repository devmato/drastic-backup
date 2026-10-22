# dRastic Backup Documentation

dRastic Backup provides a web UI, a central server, and one or more agents for running restic-based backups across hosts.

Start with [Deployment](deployment.md), then follow the [User Guide](user-guide.md).

## User Guide

- [Overview](user-guide.md) -- Core concepts and the normal workflow.
- [Agent Installation](agent-installation.md) -- Register and run backup agents.
- [TrueNAS](truenas.md) -- Deploy the agent app and back up ZFS snapshot contents.
- [Backup and Restore](backup-and-restore.md) -- Configure repositories, jobs, schedules, and restores.
- [Settings and Debug MCP](debug-access.md) -- Account settings and opt-in diagnostics.

## Deployment

- [Deployment](deployment.md) -- Docker Compose deployment for server and agent.
- [Configuration](configuration.md) -- Environment variables and runtime paths.
- [Security](security.md) -- Trust assumptions, secret handling, and backup safety.
- [Troubleshooting](troubleshooting.md) -- Common operational issues and checks.

## Development

- [Development Setup](development.md) -- Local development stack, agent workflows, tests, and builds.
- [Project Structure](project-structure.md) -- Repository layout and important files.
- [Architecture](architecture.md) -- Application boundaries, transactions and recovery rules.
- [CI and Releases](ci-and-releases.md) -- GitHub Actions, Forgejo compatibility, publishing variables, commit messages, and releases.
- [Release Checklist](release-checklist.md) -- Validate a candidate before publication.
- [Building the Docs](docs-site.md) -- Preview, build, and serving behavior for this documentation site.
