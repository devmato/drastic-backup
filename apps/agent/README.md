# dRastic Backup Agent

Background agent process for `drastic-backup`.

- Development: `./scripts/dev.sh agent-local` from the repository root; state defaults to `apps/agent/data/`.
- Docker: persistent state at `/app/data` inside the container.
- Managed native installation: `/opt/drastic-agent/data`.
- Treat identity, private keys and encrypted repository keys as sensitive state; do not share one data directory between agents.

See [Agent Installation](../../docs/agent-installation.md) and [Development Setup](../../docs/development.md#agent-checks).
