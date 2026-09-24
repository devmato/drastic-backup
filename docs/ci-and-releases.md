# CI and Releases

dRastic Backup uses GitHub Actions as the primary CI/release path. Forgejo Actions workflows are kept for Forgejo-compatible mirrors and can be validated separately.

## Branches

- `develop` is the integration branch for ongoing development.
- `main` contains released versions only.
- Release tags use the `vX.Y.Z` format.

## Commit Messages

Use Conventional Commit messages because releases and changelog entries are derived from commit history.

Format:

```text
<type>(optional-scope): <description>
```

Release-relevant types:

- `feat:` creates a minor release.
- `fix:` creates a patch release.
- `perf:` creates a patch release.
- `security:` creates a patch release.
- `deps:` creates a patch release.

Non-release types:

- `docs:`
- `chore:`
- `test:`
- `refactor:`
- `style:`
- `build:`
- `ci:`

Breaking changes:

- Add `!` after the type or scope, for example `feat(agent)!: change registration protocol`.
- Or add a `BREAKING CHANGE:` footer.

## Test Workflow

`.github/workflows/test.yml` runs on pushes to `develop` and `main`, pull requests, and manual dispatch.

Jobs:

- Python backend tests in `apps/backend`.
- Python agent tests in `apps/agent`.
- Python common library tests in `libs/python/common`.
- MariaDB migration baseline upgrade/downgrade round-trip through `apps/backend/scripts/test-migrations.sh`.
- Frontend build in `apps/frontend`.
- Documentation build with MkDocs.
- Release tooling shell syntax checks.

The matching Forgejo workflow remains under `.forgejo/workflows/test.yml` and expects a runner label named `docker`.

## Container Publishing

`.github/workflows/images.yml` publishes server and agent images as separate matrix jobs on `develop` pushes, `v*` tags, and manual dispatch. `main` is tested but does not trigger an image build: the release tag points to the same commit, so building on both would duplicate the release images. Automatic image tags include `develop` for integration and `vX.Y.Z`, `X.Y.Z`, and `latest` for releases.

GitHub publishes the default images to GitHub Container Registry:

```text
ghcr.io/devmato/drastic-backup-server
ghcr.io/devmato/drastic-backup-agent
```

The GitHub workflow uses `GITHUB_TOKEN` with `packages: write` permission and does not require additional secrets for GHCR publishing in the `devmato/drastic-backup` repository. GHCR server and agent image tags are published as multi-arch manifests for `linux/amd64` and `linux/arm64`.

Forgejo-compatible publishing remains available through `.forgejo/workflows/images.yml`, with the same triggers and separate server/agent matrix jobs. Required Forgejo variables and secrets:

- `CONTAINER_REGISTRY` -- Registry host, for example `forgejo.example.net`.
- `CONTAINER_IMAGE_NAMESPACE` -- Optional image namespace. Defaults to the owner/repository style namespace when omitted by the publishing script.
- `CONTAINER_USERNAME` -- Optional secret for registry username.
- `CONTAINER_PASSWORD` -- Optional secret for registry password.
- `FORGEJO_TOKEN` -- Secret used as fallback registry password.

## Native Agent

Native Linux agents install directly from Git with `scripts/install-drastic-agent.sh`. The installer uses the committed lockfile and a private Python runtime; no PyInstaller archives or release-asset upload jobs are built. Lifecycle tests run with the agent tests, and CI checks installer shell syntax and Python compilation.

`.github/workflows/release.yml` retains GitHub release publication and generated release notes for `v*` tags, without attaching native agent archives.

Both GitHub and Forgejo retain their server and agent Docker-image builds. Official GHCR images continue to support `linux/amd64` and `linux/arm64`.

## Git Hosting

The public upstream repository is expected to live at:

```text
https://github.com/devmato/drastic-backup
```

Forgejo should be configured as a pull mirror from GitHub when a Forgejo copy is needed. Git branches and tags are mirrored by Forgejo; GitHub Releases, release assets, issues, pull requests, and GHCR packages are not treated as synchronized state.

## Release Process

Run releases from a clean `develop` branch:

```bash
./scripts/release.sh
```

The release script validates Conventional Commit messages since the last release tag, calculates the next version, generates changelog output, merges `develop` into `main`, tags the release, pushes release refs, and returns to `develop`.

If a release must proceed despite non-conventional commits, use `-f` deliberately:

```bash
./scripts/release.sh -f
```

Do not manually bump project versions outside `./scripts/release.sh`.

## User-Facing Changelog Notes

Optional user-facing release notes can be added to:

```text
docs/changelog/UNRELEASED.md
```

That file is kept on `develop` and removed from `main` by the release script.
