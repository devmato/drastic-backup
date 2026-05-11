# dRastic Backup Documentation

dRastic Backup provides a web UI, a central server, and one or more agents for running restic-based backups across hosts.

This documentation is focused on operating the application and using it day to day.

## User Guide

- [Overview](user-guide.md) -- Core concepts and the normal workflow.
- [Agent Installation](agent-installation.md) -- Register and run backup agents.
- [Backup and Restore](backup-and-restore.md) -- Configure repositories, jobs, schedules, and restores.

## Deployment

- [Deployment](deployment.md) -- Docker Compose deployment for server and agent.
- [Configuration](configuration.md) -- Environment variables and runtime paths.
- [Security](security.md) -- Trust assumptions, secret handling, and backup safety.
- [Troubleshooting](troubleshooting.md) -- Common operational issues and checks.

## Development

- [Development Setup](development.md) -- Local development stack, agent workflows, tests, and builds.
- [Project Structure](project-structure.md) -- Repository layout and important files.
- [CI and Releases](ci-and-releases.md) -- GitHub Actions, Forgejo compatibility, publishing variables, commit messages, and releases.
- [Building the Docs](docs-site.md) -- Preview, build, and serving behavior for this documentation site.
