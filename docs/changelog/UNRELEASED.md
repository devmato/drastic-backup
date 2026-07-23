# Unreleased

## Release Notes

- Signed-in users can download a password-confirmed recovery ZIP with an offline reconstruction document containing repository credentials and backup configuration for encrypted off-site storage.
- Synchronized custom-repository schedules can continue while the backend is offline only when the repository and local agent key remain available; native repositories require the backend proxy.
- Schedule slots are persistently deduplicated, missed slots are not caught up, and busy slots are skipped without retry.
- Backup warnings now distinguish post-backup check, retention, statistics, and hook failures from failed backups; failed checks skip retention.
- Retention reconciles snapshot tags conservatively and separates forget from prune. Restores default to collision-safe behavior and mark partial destinations after an interrupted started restore.
- Failed notifications remain pending for retry on later operation reports, and interrupted native repository deletion is reconciled through quarantine.
- Alembic now uses a fresh-database baseline. No upgrade path from earlier development schemas is supplied because there were no production deployments to migrate; the MariaDB migration round-trip is covered by `apps/backend/scripts/test-migrations.sh`.
