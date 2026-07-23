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

Server and agent image workflows publish container images on branch pushes, release tags, and manual dispatch depending on the workflow.

GitHub publishes the default images to GitHub Container Registry:

```text
ghcr.io/devmato/drastic-backup-server
ghcr.io/devmato/drastic-backup-agent
```

The GitHub workflows use `GITHUB_TOKEN` with `packages: write` permission and do not require additional secrets for GHCR publishing in the `devmato/drastic-backup` repository. GHCR server and agent image tags are published as multi-arch manifests for `linux/amd64` and `linux/arm64`.

Forgejo-compatible publishing remains available through `.forgejo/workflows/server-image.yml` and `.forgejo/workflows/agent-image.yml`. Required Forgejo variables and secrets:

- `CONTAINER_REGISTRY` -- Registry host, for example `forgejo.example.net`.
- `CONTAINER_IMAGE_NAMESPACE` -- Optional image namespace. Defaults to the owner/repository style namespace when omitted by the publishing script.
- `CONTAINER_USERNAME` -- Optional secret for registry username.
- `CONTAINER_PASSWORD` -- Optional secret for registry password.
- `FORGEJO_TOKEN` -- Secret used as fallback registry password and for release asset uploads.

## Agent Artifact

The GitHub agent artifact workflow builds Linux amd64 and arm64 archives for release distribution and attaches them to GitHub releases on `v*` tags. Artifact names follow the backend artifact template, for example `drastic-agent-linux-amd64-v0.1.0.tar.gz` and `drastic-agent-linux-arm64-v0.1.0.tar.gz`. Forgejo agent artifact publishing remains amd64-only.

Native installers download public release assets through the backend cache. The backend builds release download URLs in this form for GitHub, Forgejo, and Gitea:

```text
<base-url>/<owner>/<repo>/releases/download/<tag>/<asset-name>
```

When `DRASTIC_AGENT_ARTIFACT_TAG` is empty, the backend derives the tag from its package version, for example backend version `0.1.0` resolves to release tag `v0.1.0`.

The build helper is:

```bash
./scripts/build-agent-artifact.sh
```

GitHub release assets are uploaded with the GitHub release workflow. Forgejo release assets are still supported by the Forgejo-specific CI helper under `scripts/ci/`.

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
