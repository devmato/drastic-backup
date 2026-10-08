# Contributing

## Branches

- `develop` is the integration branch for ongoing development.
- `main` contains released versions only.
- Releases are created from `develop` with `./scripts/release.sh`, which merges `develop` into `main`, tags its final commit as `YYYY-MM-DD-<8-character SHA>`, pushes `develop`, `main`, and the tag, then returns to `develop`.
- GitHub is the primary public upstream at `github.com/devmato/drastic-backup`; Forgejo copies should be pull mirrors when used.

## Commit Messages

Use Conventional Commit messages because release eligibility and changelog entries are derived from the commit history. Versions identify source commits, not semantic version increments.

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

- `feat:`, `fix:`, `perf:`, `security:`, and `deps:` qualify for a release.
- A `!` after the type or scope, or a `BREAKING CHANGE:` footer, qualifies for a release and highlights the incompatibility in its notes.
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

With `-f`, non-conventional commits are ignored for changelog generation. At least one release-relevant Conventional Commit is still required.

Backend and agent versions use the commit date in UTC and the first eight SHA characters, for example `2026-10-08-a1b2c3d4`. Docker builds and native installations stamp this value into the shared Python package; development checkouts read it from Git. Local modifications append `-dirty`. Missing metadata reports `unknown` rather than a package version. Protocol numbers still control agent compatibility.

The `version` fields required by Python/npm packaging stay fixed and are not product versions. Do not bump them. The generated `build-version.txt` is not committed. Docker builds use the repository root as their context. CI passes the source version as the `DRASTIC_VERSION` build argument; without it, the build reads Git metadata from the context. For linked Git worktrees, generate the version on the host and pass it explicitly (see [local Docker builds](docs/ci-and-releases.md#local-docker-builds)). Git metadata is never copied into the runtime images.

## Backup Selection UI

- Prefer the same split-view layout for all backup job types: a hierarchical browser on the left, explicit selections and exclusions on the right. Reuse `PathBrowser`, `PathSelectionPanel`, and `SelectionLayout`.
- Express selection scope through **+ / −** actions. Selecting a parent includes supported descendants, including newly created ones. Derive the selection mode from the selection instead of exposing separate selection-mode or child-inclusion switches.
- Do not show selection-count badges; the selection list already communicates the scope.
- Place job-specific controls, such as **Backup Mode**, below the split view. Put optional settings under **Advanced**.
- Use consistent type icons in both columns: pools/datasets `storage`, folders `folder`, files `description`, hosts `dns`, and VMs `computer`.
- Keep source identities separate from display labels and paths so equally named entries remain independently selectable.

These are defaults, not requirements when a backup type cannot represent them faithfully. Use the simplest suitable alternative for atomic sources (such as whole-database backups), dynamic selection rules (such as VM tags), or non-hierarchical sources. Reuse applicable shared components and limit deviations to what the backup semantics require. Only offer inheritance and exclusions where the backup implementation supports them.

## CI Providers

GitHub Actions is the primary CI/release provider. GitHub publishes default container images to:

- `ghcr.io/devmato/drastic-backup-server`
- `ghcr.io/devmato/drastic-backup-agent`

GitHub release tags trigger server and agent image publishing and GitHub release notes. Native agents install directly from Git.

## Forgejo Actions

Forgejo workflows remain in `.forgejo/workflows` for Forgejo-compatible mirrors. They expect a runner label named `docker`.

Container image publishing uses these Forgejo variables and secrets:

- `CONTAINER_REGISTRY`: registry host, for example `forgejo.example.net`.
- `CONTAINER_IMAGE_NAMESPACE`: optional image namespace, defaults to `owner/repository`.
- `CONTAINER_USERNAME`: optional secret for the registry username, defaults to the Actions actor.
- `CONTAINER_PASSWORD`: optional secret for the registry password.
- `FORGEJO_TOKEN`: secret used as fallback registry password and for release asset uploads.

Forgejo release tags trigger server and agent image publishing once Forgejo variables, secrets, and runner access are configured.
