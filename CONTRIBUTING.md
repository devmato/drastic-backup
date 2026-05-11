# Contributing

## Branches

- `develop` is the integration branch for ongoing development.
- `main` contains released versions only.
- Releases are created from `develop` with `./scripts/release.sh`, which merges `develop` into `main`, creates a `vX.Y.Z` tag, pushes `develop`, `main`, and the tag, then returns to `develop`.
- GitHub is the primary public upstream at `github.com/devmato/drastic-backup`; Forgejo copies should be pull mirrors when used.

## Commit Messages

Use Conventional Commit messages because releases, versions, and changelog entries are derived from the commit history.

Format:

```text
<type>(optional-scope): <description>
```

Examples:

```text
feat(agent): add scheduled backup execution
fix(backend): repair repository assignment
perf(frontend): reduce restore polling overhead
docs: update production setup
chore: update dependencies
```

Release impact:

- `fix:` creates a patch release.
- `perf:` creates a patch release.
- `security:` creates a patch release.
- `deps:` creates a patch release.
- `feat:` creates a minor release.
- A `!` after the type or scope creates a major release.
- A `BREAKING CHANGE:` footer creates a major release.
- `docs:`, `chore:`, `test:`, `refactor:`, `style:`, `build:`, and `ci:` do not create a release by themselves.

Breaking change examples:

```text
feat(agent)!: change registration protocol
```

```text
feat(agent): change registration protocol

BREAKING CHANGE: Existing agents must re-register after upgrading.
```

## Changelog Notes

`CHANGELOG.md` is generated during releases from Conventional Commit messages. Optional user-facing notes can be added to `docs/changelog/UNRELEASED.md` before running the release.

`docs/changelog/UNRELEASED.md` is kept on `develop` and removed from `main` by the release script.

After tagging `main`, the release script fast-forwards `develop` to the tagged release state and reopens `docs/changelog/UNRELEASED.md` there for the next development cycle.

## Releases

Run releases from a clean `develop` branch:

```bash
./scripts/release.sh
```

The script aborts when it finds non-conventional commit messages since the last release tag. If a release must proceed anyway, use `-f` deliberately:

```bash
./scripts/release.sh -f
```

With `-f`, non-conventional commits are ignored for version calculation and changelog generation.

## CI Providers

GitHub Actions is the primary CI/release provider. GitHub publishes default container images to:

- `ghcr.io/devmato/drastic-backup-server`
- `ghcr.io/devmato/drastic-backup-agent`

GitHub release tags trigger server image publishing, agent image publishing, and the Linux x64 agent artifact build.

## Forgejo Actions

Forgejo workflows remain in `.forgejo/workflows` for Forgejo-compatible mirrors. They expect a runner label named `docker`.

Container image publishing uses these Forgejo variables and secrets:

- `CONTAINER_REGISTRY`: registry host, for example `forgejo.example.net`.
- `CONTAINER_IMAGE_NAMESPACE`: optional image namespace, defaults to `owner/repository`.
- `CONTAINER_USERNAME`: optional secret for the registry username, defaults to the Actions actor.
- `CONTAINER_PASSWORD`: optional secret for the registry password.
- `FORGEJO_TOKEN`: secret used as fallback registry password and for release asset uploads.

Forgejo release tags can trigger server image publishing, agent image publishing, and the Linux x64 agent artifact build once Forgejo variables, secrets, and runner access are configured.
