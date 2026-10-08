# CI and Releases

dRastic Backup uses GitHub Actions as the primary CI/release path. Forgejo Actions workflows are kept for Forgejo-compatible mirrors and can be validated separately.

## Branches

- `develop` is the integration branch for ongoing development.
- `main` contains released versions only.
- Release tags use `YYYY-MM-DD-<8-character SHA>`, for example `2026-10-08-a1b2c3d4`.

## Commit Messages

Use Conventional Commit messages because releases and changelog entries are derived from commit history.

Format:

```text
<type>(optional-scope): <description>
```

Release-relevant types:

- `feat:`, `fix:`, `perf:`, `security:`, and `deps:` qualify for a release. There are no major/minor/patch increments.

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

`.github/workflows/images.yml` publishes server and agent images as separate matrix jobs on `develop` pushes, date/SHA release tags, and manual dispatch. `main` is tested but does not trigger an image build: the release tag points to the same commit, so building on both would duplicate the release images. Every image has a `YYYY-MM-DD-<8-character SHA>` tag. Moving aliases are `develop` for integration and `latest` for releases.

Docker builds and native installations use the same `libs/python/common/src/drastic_common/version.py` helper. It combines the commit date in UTC with the first eight SHA characters and appends `-dirty` for local changes. The version is stamped into `drastic_common/build-version.txt` before packaging, so installed software needs no Git for version reporting. Development checkouts read Git directly; missing metadata reports `unknown`. Agent properties and debug diagnostics expose the same backend/agent versions. Protocol compatibility remains independent of version equality.

CI computes the version on the host and passes it through the `DRASTIC_VERSION` build argument. Python/npm package version fields are fixed packaging placeholders and are neither bumped nor reported as product versions.

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

### Local Docker Builds

Use the repository root as the Docker build context. In a normal checkout, both Dockerfiles determine the version automatically from Git. Git metadata is mounted read-only in the version build stage and never copied into the runtime image.

A linked Git worktree stores its Git metadata outside that context. Compute its version on the host and pass it explicitly, using the same helper as CI and native installations:

```bash
VERSION=$(python3 libs/python/common/src/drastic_common/version.py)
docker build --build-arg "DRASTIC_VERSION=$VERSION" -f Dockerfile -t drastic-backend:local .
docker build --build-arg "DRASTIC_VERSION=$VERSION" -f apps/agent/Dockerfile -t drastic-agent:local .
```

Recompute the value after changing the source; local modifications retain the `-dirty` suffix. With an explicit version, the build does not need usable Git metadata inside its context.

The optional Docker regression check exercises both version build stages with ordinary checkouts and clean/dirty linked worktrees:

```bash
DRASTIC_TEST_DOCKER=1 uv run --project libs/python/common pytest libs/python/common/tests/test_version.py
```

## Native Agent

Native Linux agents install directly from Git with `scripts/install-drastic-agent.sh`. The installer uses the committed lockfile and a private Python runtime; no PyInstaller archives or release-asset upload jobs are built. Lifecycle tests run with the agent tests, and CI checks installer shell syntax and Python compilation.

`.github/workflows/release.yml` publishes GitHub releases and generated release notes for date/SHA tags, without attaching native agent archives.

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

The release script validates Conventional Commit messages since the last release tag, generates changelog output, merges `develop` into `main`, and removes the unreleased notes there. Only then does it derive the version from the final `main` commit and tag it. It fast-forwards `develop`, reopens the unreleased notes, and pushes both branches and the tag. Legacy `vX.Y.Z` tags are accepted as the baseline for the first date/SHA release.

Committed changelog sections use a release date; GitHub release notes carry the exact version tag. A commit cannot contain its own hash, so the generated version is never committed. Package manifests and lockfiles are not rewritten during releases.

If a release must proceed despite non-conventional commits, use `-f` deliberately:

```bash
./scripts/release.sh -f
```

Use the shared helper to inspect a checkout's version:

```bash
python3 libs/python/common/src/drastic_common/version.py
```

## User-Facing Changelog Notes

Optional user-facing release notes can be added to:

```text
docs/changelog/UNRELEASED.md
```

That file is kept on `develop` and removed from `main` by the release script.
