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
12. Verify migrations work from an empty database.
13. Confirm server and agent images use matching release tags.
