# Unreleased

## Release Notes

- Restic backups now use a stable agent-based hostname across container recreations unless `RESTIC_HOST` is explicitly configured. The first file backup after this change may reread unchanged files; repository deduplication is preserved. Retention skips redundant prune runs and persists pending cleanup for retry after failure or restart.

- Operation start/end times, logs and last-run timestamps now include an explicit UTC offset, fixing incorrect local times and running durations for UTC containers viewed from another timezone. Update both server and agent. Existing naive database timestamps are interpreted as UTC; historical values from other timezones are not automatically shifted.

- Managed Docker agents now support WebUI updates using the same Git lifecycle installer as native agents, including required Debian packages and startup rollback. Updates survive container restarts; recreating the container restores the selected image's software and packages while preserving agent data. Older deployments need one image update to enable protocol 4 support.

- Agents can now have an optional display name, editable on their Info tab even while offline. Aliases replace hostnames in the UI and recovery reports and persist across reconnects; clearing an alias restores the hostname display.

- Agent properties now have a full detail page with Proxmox and TrueNAS connections. Protocol 3 agents expose backup types according to their configuration. TrueNAS jobs select filesystem datasets, back up temporary ZFS snapshot contents through Restic and track cleanup across interrupted runs. See the new TrueNAS Custom App installation guide for snapshot mount requirements.

- Proxmox API access can now be configured and tested in the agent properties, with a shortcut from the backup job form. Tokens are stored encrypted on the agent and settings apply without restarting. Tab content switches are now instant throughout the web UI.
- Native Linux agents now install directly from Git through a pipe installer, with private Python/uv, local lifecycle commands and update rollback. Native release archives and their backend cache have been removed; Docker image builds remain available. Existing native installations must first uninstall without `--purge` to retain their identity, then run the new installer with `--reuse-data`.
- Signed-in users can download a password-confirmed recovery ZIP with an offline reconstruction document containing repository credentials and backup configuration for encrypted off-site storage.
- Synchronized custom-repository schedules can continue while the backend is offline only when the repository and local agent key remain available; native repositories require the backend proxy.
- Schedule slots are persistently deduplicated, missed slots are not caught up, and busy slots are skipped without retry.
- Backup warnings now distinguish post-backup check, retention, statistics, and hook failures from failed backups; failed checks skip retention.
- Retention reconciles snapshot tags conservatively and separates forget from prune. Restores default to collision-safe behavior and mark partial destinations after an interrupted started restore.
- Failed notifications remain pending for retry on later operation reports, and interrupted native repository deletion is reconciled through quarantine.
- Alembic now uses a fresh-database baseline. No upgrade path from earlier development schemas is supplied because there were no production deployments to migrate; the MariaDB migration round-trip is covered by `apps/backend/scripts/test-migrations.sh`.
- Application defaults now live in code for identical Docker and native-agent behavior. Production deployments use a single `.env`; migrate server values from an existing `.env.prod` using the current `.env.example`.
