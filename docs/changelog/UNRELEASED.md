# Unreleased

## Release Notes

- Bootstrap admin seeding now leaves existing users unchanged unless an explicit update flag is set.
- Repository passwords now use per-user recovery-key envelopes while settings secrets derive encryption from `DRASTIC_APP_MASTER_SECRET`.
- Secret repository operations now use an inline password confirmation dialog instead of requiring a full login redirect when the in-memory recovery key is missing.
- Added Homelab security guidance and a release checklist.
