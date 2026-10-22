# Release Checklist

Start **Actions → Prepare Release → Run workflow** on `develop`, then run this checklist using the resulting candidate's exact versioned images before clicking **Publish release** on its draft. Automated tests do not replace the real backup and restore checks below. `main` and `latest` stay on the previous release during candidate testing.

## Candidate and Deployment

- [ ] Pin server/agent images and native source to the same candidate commit; use an explicitly tested rest-server tag.
- [ ] Generate a fresh production `.env`, start the stack, sign in and register an agent from a trusted network.
- [ ] Render `docker compose -f docker-compose.yaml config`; confirm the intended bind address.
- [ ] Test HTTPS login/logout with secure cookies and an agent WebSocket through the reverse proxy, preserving host/scheme headers.
- [ ] Confirm CI lint/tests, both image architectures and the fresh MariaDB migration round-trip passed. Do not claim unsupported historical schema upgrades.

## Backup and Restore

- [ ] Create a native repository, run a small file backup, restore to an empty temporary path and compare contents.
- [ ] Restart server/agent, repeat the backup and confirm reports update.
- [ ] Check `fail_if_exists`, explicit overwrite confirmation and partial-destination reporting after interrupted restore.
- [ ] Verify calendar schedules use the account zone; missed/busy slots do not replay. Verify eligible temporary-failure retries separately from skipped slots.
- [ ] Disconnect the backend: a synchronized custom target works only while directly reachable with local key material; native storage requires the proxy.

## Failure and Cleanup

- [ ] Check schedule deduplication, interrupted reports, notification retry and repository-quarantine recovery using the existing reliability tests.
- [ ] Force a post-backup integrity failure: result is warning, retention is blocked, statistics/success/end hooks are attempted. A pre-backup failure stays failed.
- [ ] Verify retention without report history, foreign/legacy/ambiguous ownership protection, and the newest-complete-run safety floor.
- [ ] Interrupt completion tagging: the backup ends with a pending warning, deletion stays blocked, and the next repository backup or manual retention recovers it. The failed chain must not resume automatically.
- [ ] Verify confirmed removals survive prune failure/restart and a later prune reclaims space. Do not use forced unlock to bypass active work.

## Platform Lifecycle

- [ ] Test native install/update/startup rollback/uninstall while retaining identity; test Docker image recreation with persistent data.
- [ ] For macOS changes, perform the [real-device acceptance check](development.md#macos-acceptance-check).
- [ ] For TrueNAS changes, perform the [snapshot smoke test](truenas.md#snapshot-smoke-test-on-truenas-25104).
- [ ] For Proxmox backup/restore changes, use the [live checks](development.md#live-proxmox-checks), then boot and verify a restored guest.

## Disaster Recovery

- [ ] Follow the [control-plane backup/restore procedure](backup-and-restore.md#back-up-the-control-plane), stopping agents/writers before state copies and restoring in isolation.
- [ ] Verify a direct-Restic emergency restore from a consistent repository copy without the control plane, using independently retained credentials.
