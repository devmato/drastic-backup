# CI and Releases

dRastic Backup uses GitHub Actions as the primary CI/release path. Forgejo Actions workflows are kept for Forgejo-compatible mirrors and can be validated separately.

## Branches

- `develop` is the default integration branch and the normal pull request target.
- `main` points to the last approved source commit, without extra release or merge commits.
- Release tags use `YYYY-MM-DD-<8-character SHA>`, for example `2026-10-08-a1b2c3d4`.
- The date is the UTC commit date, not the publication date. Approving a commit does not change its backend/agent version.

## Commit Messages

Use Conventional Commit messages to derive changelog entries from commit history. Release timing is a manual decision, independent of commit types.

Format:

```text
<type>(optional-scope): <description>
```

Dedicated changelog sections:

- `feat:`, `fix:`, `perf:`, `security:`, and `deps:`. There are no major/minor/patch increments.

Other Changes (also releasable):

- `docs:`
- `chore:`
- `test:`
- `refactor:`
- `style:`
- `build:`
- `ci:`
- `revert:`

Breaking changes:

- Add `!` after the type or scope, for example `feat(agent)!: change registration protocol`.
- Or add a `BREAKING CHANGE:` footer.
- Describe the user-visible incompatibility and required migration steps; an internal refactor alone is not a breaking change.

## Test Workflow

`.github/workflows/test.yml` runs on pushes to `develop` and `main`, pull requests, and manual dispatch. Release preparation calls the same workflow on the selected, already-pushed `develop` SHA. There is no generated source commit or Git bundle.

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

`.github/workflows/images.yml` publishes server and agent images as separate matrix jobs on `develop` pushes or manual dispatch on `develop`. The release workflow calls the same build jobs with the tested release tag. Neither `main` pushes nor tag pushes start separate GitHub image builds. Every image has a `YYYY-MM-DD-<8-character SHA>` tag. Moving aliases are `develop` for integration and `latest` for releases.

Image builds never update `latest` themselves. Existing versioned images are reused rather than rebuilt, including on retries; registry errors other than a missing manifest stop the build. GitHub serializes builds of the same image/commit across development and release preparation.

Publishing the draft triggers `.github/workflows/publish-release.yml`. It verifies successful preparation, checks both images, fast-forwards `main`, then promotes the images without rebuilding. Registry alias updates are separate operations: if promotion fails partway through, rerun that publication workflow to finish the same release.

Docker builds and native installations use the same `libs/python/common/src/drastic_common/version.py` helper. It combines the commit date in UTC with the first eight SHA characters and appends `-dirty` for local changes. The version is stamped into `drastic_common/build-version.txt` before packaging, so installed software needs no Git for version reporting. Development checkouts read Git directly; missing metadata reports `unknown`. Agent properties and debug diagnostics expose the same backend/agent versions. Protocol compatibility remains independent of version equality.

CI computes the version on the host and passes it through the `DRASTIC_VERSION` build argument. Python/npm package version fields are fixed packaging placeholders and are neither bumped nor reported as product versions.

GitHub publishes the default images to GitHub Container Registry:

```text
ghcr.io/devmato/drastic-backup-server
ghcr.io/devmato/drastic-backup-agent
```

The GitHub workflow uses `GITHUB_TOKEN` with `packages: write` permission and does not require additional secrets for GHCR publishing in the `devmato/drastic-backup` repository. GHCR server and agent image tags are published as multi-arch manifests for `linux/amd64` and `linux/arm64`.

Forgejo-compatible publishing remains available through `.forgejo/workflows/images.yml`, on `develop`/`main` pushes, date/SHA tags and manual dispatch. Tags identify candidates and only build versioned images. Once the approved `main` is mirrored, the two image jobs reuse or build that commit's images and a dependent job promotes both `latest` aliases in the configured registry. Forgejo never advances `main` itself. Image workflows are serialized to protect version tags and stable aliases. Configure the pull mirror to trigger actions on ref updates, plus a runner with Docker access; the workflow installs Buildx. Required Forgejo variables and secrets:

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

`.github/workflows/release.yml` prepares GitHub drafts with Conventional Commit notes, without attaching native agent archives. Draft candidates can already install native agents from the exact backend commit.

Both GitHub and Forgejo retain their server and agent Docker-image builds. Official GHCR images continue to support `linux/amd64` and `linux/arm64`.

## Git Hosting

The public upstream repository is expected to live at:

```text
https://github.com/devmato/drastic-backup
```

Forgejo should be configured as a pull mirror from GitHub when a Forgejo copy is needed. Git branches and tags are mirrored by Forgejo; GitHub Releases, release assets, issues, pull requests, and GHCR packages are not treated as synchronized state.

## Release Process

1. Merge reviewed changes into `develop`. For squash merges, use a Conventional Commit PR title as the resulting commit message.
2. Open **Actions → Prepare Release → Run workflow** and select **develop**. There are no version or prerelease inputs. GitHub fixes the source SHA when the run is started; later pushes do not change it.
3. The workflow tests that commit, validates Conventional Commit notes, pushes only its immutable date/SHA tag, and builds or reuses both versioned images. `develop`, `main` and `latest` are not changed.
4. After both builds succeed, a GitHub **draft** appears under **Releases**. Edit its Markdown description to add update instructions, known limitations or clearer summaries. Complete the [Release Checklist](release-checklist.md) against the candidate's exact image tags. If a code fix is needed, prepare the new commit as a new version and discard the outdated draft; never move its tag.
5. Click **Publish release** in the draft. This makes the release text public immediately and triggers **Publish Release**. That workflow verifies successful preparation of the commit and both images before fast-forwarding `main` and updating the `latest` aliases. Wait for it to finish successfully before treating the technical rollout as complete. It does not rebuild images, edit release notes or touch `develop`.

Preparation and approval share a concurrency group with `queue: max` (up to 100 pending runs), so a new preparation does not replace a waiting approval. The built-in `GITHUB_TOKEN` supplies scoped `contents: write`, `packages: write` and, for approval, `actions: read`. Repository rules must permit the workflow's tag and `main` pushes. No personal access token is required. Token-authored tag pushes do not start additional push workflows, so preparation calls tests and image builds explicitly. A human publishing the draft triggers the approval workflow. Standard releases are supported; marking the draft as a prerelease does not advance `main` or `latest`.

### Failed Runs

Use **Re-run failed jobs** or **Re-run all jobs** on the original workflow run after correcting an external failure. GitHub retains its selected SHA, existing tags/images are reused, and an existing draft or published release is never edited or reset. No stored Git bundle is needed. Code changes require a new preparation run on the new commit.

Newer `develop` commits are left alone. An older candidate cannot advance `main` or `latest` after a newer release was approved. A build failure may leave a candidate tag, but cannot change `main` or stable images. A failed approval may leave the GitHub release visible or only part of the technical promotion complete; finish that workflow before starting another release. Keep the successful preparation run until approval, because the approval workflow checks its result.

### Local Preparation

From a clean checkout of the intended source commit (branch or detached HEAD):

```bash
./scripts/release.sh
# For a later release, pass the last published release tag explicitly:
./scripts/release.sh 2026-10-08-a1b2c3d4
```

The script validates Conventional Commits, prints Markdown notes to stdout and creates the local source-version tag if it does not already exist. It never commits, switches branches, pushes, or writes tracked files. An existing tag must identify the same commit. The first release needs neither a baseline tag nor `main`. Redirect output outside the checkout to avoid making it dirty before validation.

The workflow chooses the most recently published non-prerelease as the baseline, excluding the current candidate on retries. Drafts and abandoned tags do not hide changes from the notes. Local callers supply that baseline explicitly; the script does not guess it from the nearest Git tag. Package manifests and lockfiles are not rewritten during releases.

Use the shared helper to inspect a checkout's version:

```bash
python3 libs/python/common/src/drastic_common/version.py
```

## Release Notes

[GitHub Releases](https://github.com/devmato/drastic-backup/releases) are the changelog. Generated sections come from Conventional Commits and include commit/compare links; `BREAKING CHANGE:` footers retain their detailed instructions. Edit the complete text directly in the draft, without a source commit. Later corrections can be made with **Edit release**. Retries preserve both draft and published text.

The earlier manual notes are archived in [Initial Release Notes](initial-release-notes.md) and included only when no previous published release exists. This is a one-time seed, not a second ongoing changelog. Both branches contain the same source files.

From the first public release onward, database changes must provide tested upgrade migrations from released schemas. Replacing the baseline is only appropriate for unreleased development databases.
