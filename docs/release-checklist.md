# Release Checklist

Run this checklist before tagging a Homelab release.

1. Generate a fresh production environment file with `./scripts/generate-env.sh prod`.
2. Start the server stack with the generated environment file.
3. Sign in with the bootstrap admin account.
4. Register a Docker or native agent from a trusted network.
5. Create a native repository.
6. Create and run a file backup job against a small test directory.
7. Confirm a snapshot appears for the job.
8. Restore at least one file or directory into a temporary target path.
9. Compare restored content with the source content.
10. Restart the server and agent containers or services.
11. Run the job again and confirm reports still update.
12. Run `apps/backend/scripts/test-migrations.sh` and verify the fresh Alembic baseline round-trip; do not claim an upgrade path from older development schemas.
13. Run the backend and agent reliability tests covering persistent schedule deduplication and skipped slots, interrupted-operation recovery, notification retry, retention reconciliation, and native repository quarantine recovery.
14. Disconnect the backend and verify a synchronized custom-repository schedule runs only while its target and local agent key remain available; verify a native-repository run fails without the backend proxy.
15. Verify missed schedule slots are not caught up and a busy job or repository records the current slot as skipped without a later retry.
16. Force a post-backup check failure and verify the backup is `warning`, retention is skipped, and statistics plus `success` and `end` hooks are still attempted. Verify pre-backup failures remain `failed` and successful post-backup steps produce `success`.
17. Verify retention skips a snapshot with mismatched artifact tags, reports a warning, and performs separate forget and prune steps without repeating forget after a prune retry.
18. Verify restore defaults to `fail_if_exists`, explicit overwrite requires confirmation, and an interrupted started restore is failed with partial-destination markers.
19. Confirm server and agent images and the native agent Git ref use the same release commit. Verify native install, update rollback and uninstall; Docker-image publishing must cover both supported architectures.
20. Confirm the rest-server image tag in Compose (or `DRASTIC_REST_SERVER_VERSION` override) is an explicitly tested version.
21. Render `docker compose -f docker-compose.yaml config` and verify the backend publishes only on the intended host address.
22. Test HTTPS login and logout through the reverse proxy with secure cookies enabled.
23. Test an agent WebSocket connection through the proxy and verify `Host` and `X-Forwarded-Proto` are preserved.
24. Create a MariaDB logical dump, save the production environment/secret store, and verify the documented control-plane restore procedure in an isolated environment.
25. Stop the stack or use a storage snapshot, copy the native repository storage, and verify a direct-restic emergency restore without the control plane.
