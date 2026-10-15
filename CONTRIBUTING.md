# Contributing

## Architecture And Code Style

Read [Architecture](docs/architecture.md) for module responsibilities, dependency direction and durable backup flows, and [Project Structure](docs/project-structure.md) for their locations.

- Implement complete use cases in business services, keeping HTTP/Socket.IO handlers focused on their transport contracts.
- Keep shared state in frontend stores, HTTP calls in API modules and multi-step editor logic in composables.
- Reuse existing services and libraries before introducing abstractions. Small modules can remain single files.
- Add concise English comments/docstrings for purpose, surprising constraints and failure/recovery behavior. Prefer explaining **why** rather than narrating the code.
- Treat public API payloads, agent protocol compatibility and persisted backup state as contracts during refactors. Update affected tests and documentation together with the code.

## Branches

- `develop` is the default branch for ongoing development and the normal pull request target.
- `main` points to the last approved release commit. Feature PRs target `develop`, not `main`.
- Start GitHub Actions **Prepare Release → Run workflow** on `develop`. After tests and images succeed, edit the generated GitHub draft and click **Publish release**. The automatic **Publish Release** workflow advances `main` and `latest` to that exact commit without additional commits.
- GitHub is the primary public upstream at `github.com/devmato/drastic-backup`; Forgejo copies should be pull mirrors when used.

## Commit Messages

Use Conventional Commit messages to derive changelog entries from the commit history. Versions identify source commits, not semantic version increments. Releases are a manual decision, independent of commit types.

For squash merges, use a Conventional Commit PR title as the resulting commit message. For other merge methods, keep the individual commits conventional. Merge commits are excluded from generated release notes.

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

Changelog groups:

- `feat:`, `fix:`, `perf:`, `security:`, and `deps:` have dedicated changelog sections.
- A `!` after the type or scope, or a `BREAKING CHANGE:` footer, highlights an incompatibility. Explain who is affected and what they need to do.
- `docs:`, `chore:`, `test:`, `refactor:`, `style:`, `build:`, `ci:`, and `revert:` appear under Other Changes and can also be released.

Breaking change examples:

```text
feat(agent)!: change registration protocol
```

```text
feat(agent): change registration protocol

BREAKING CHANGE: Existing agents must re-register after upgrading.
```

## Releases

Prepare a candidate, complete the [release checklist](docs/release-checklist.md) using its exact versioned images, then publish the draft. There are no version or prerelease inputs. `main` and `latest` stay on the previous release until approval. New work on `develop` does not change a draft; code fixes need a new candidate.

[GitHub Releases](https://github.com/devmato/drastic-backup/releases) are the changelog. Generated notes cover Conventional Commits since the last published release, not an abandoned candidate tag. Edit update instructions and other manual notes directly in the draft. Retries never replace an existing release description. The original development notes are preserved in `docs/initial-release-notes.md` and seed only the first draft.

For local note generation and tagging, use `./scripts/release.sh [previous-published-tag]` from a clean checkout. It prints Markdown and creates a local tag without committing, switching branches or pushing. Do not redirect its output to an untracked file inside the checkout. For the complete workflow and retries, see [CI and Releases](docs/ci-and-releases.md#release-process).

Backend and agent versions use the commit date in UTC and the first eight SHA characters, for example `2026-10-08-a1b2c3d4`. Docker builds and native installations stamp this value into the shared Python package; development checkouts read it from Git. Local modifications append `-dirty`. Missing metadata reports `unknown` rather than a package version. Protocol numbers still control agent compatibility.

The `version` fields required by Python/npm packaging stay fixed and are not product versions. Do not bump them. The generated `build-version.txt` is not committed. Docker builds use the repository root as their context. CI passes the source version as the `DRASTIC_VERSION` build argument; without it, the build reads Git metadata from the context. For linked Git worktrees, generate the version on the host and pass it explicitly (see [local Docker builds](docs/ci-and-releases.md#local-docker-builds)). Git metadata is never copied into the runtime images.

## Backup Selection UI

- Prefer the same split-view layout for all backup job types: a hierarchical browser on the left, explicit selections and exclusions on the right. Reuse `PathBrowser`, `PathSelectionPanel`, and `SelectionLayout`.
- Express selection scope through **+ / −** actions. Selecting a parent includes supported descendants, including newly created ones. Derive the selection mode from the selection instead of exposing separate selection-mode or child-inclusion switches.
- The most specific browser path rule wins: an explicit child include can override a parent exclusion, and a deeper exclusion can narrow that child again. Removing a rule restores inheritance. Show explicit versus inherited scope, and keep explicit child selections visible when a parent is selected or removed. Free-form exclude patterns retain Restic semantics.
- Do not show selection-count badges; the selection list already communicates the scope.
- Place job-specific controls, such as **Backup Mode**, below the split view. Put optional settings under **Advanced**.
- Use consistent type icons in both columns: pools/datasets `storage`, folders `folder`, files `description`, hosts `dns`, and VMs `computer`.
- Keep source identities separate from display labels and paths so equally named entries remain independently selectable.

These are defaults, not requirements when a backup type cannot represent them faithfully. Use the simplest suitable alternative for atomic sources (such as whole-database backups), dynamic selection rules (such as VM tags), or non-hierarchical sources. Reuse applicable shared components and limit deviations to what the backup semantics require. Only offer inheritance and exclusions where the backup implementation supports them.

## CI Providers

GitHub Actions is the primary CI/release provider. GitHub publishes default container images to:

- `ghcr.io/devmato/drastic-backup-server`
- `ghcr.io/devmato/drastic-backup-agent`

GitHub's preparation workflow explicitly calls tests and image builds; tag pushes do not start duplicate builds. Native agents install directly from Git.

## Forgejo Actions

Forgejo workflows remain in `.forgejo/workflows` for Forgejo-compatible mirrors. They expect a runner label named `docker`.

Container image publishing uses these Forgejo variables and secrets:

- `CONTAINER_REGISTRY`: registry host, for example `forgejo.example.net`.
- `CONTAINER_IMAGE_NAMESPACE`: optional image namespace, defaults to `owner/repository`.
- `CONTAINER_USERNAME`: optional secret for the registry username, defaults to the Actions actor.
- `CONTAINER_PASSWORD`: optional secret for the registry password.
- `FORGEJO_TOKEN`: secret used as fallback registry password.

Forgejo candidate tags build versioned images. Mirroring the approved `main` triggers both image jobs and, after they succeed, promotion to `latest` in the configured registry. Existing versioned images are reused. Configure variables, secrets, Docker/Buildx access and a pull mirror that triggers actions on ref updates.
