# Agent Guidelines

## Git And Releases

- Use Conventional Commit messages because release notes are derived from commit messages.
- Use `feat:` for release-relevant new functionality.
- Use `fix:`, `perf:`, `security:`, or `deps:` for release-relevant fixes and maintenance.
- Use `docs:`, `chore:`, `test:`, `refactor:`, `style:`, `build:`, or `ci:` for changes that should not trigger a release by themselves.
- Mark intentional breaking changes with `!` in the commit header or a `BREAKING CHANGE:` footer.
- The only product version is `YYYY-MM-DD-<8-character SHA>` from the commit date in UTC and commit hash. Local changes append `-dirty`.
- Use `drastic_common/version.py` for version generation. Package metadata versions are fixed packaging placeholders, not product versions; do not bump them.
- Use `./scripts/release.sh` to tag the final `main` commit; never commit its generated version into that commit.
- Use `chore(release): ...` for release preparation commits.
- `develop` is the integration branch. `main` contains released versions only.
- `docs/changelog/UNRELEASED.md` is kept on `develop` for optional user-facing release notes and is removed from `main` by the release script.

## Tests And Verification

- Match verification effort to the risk and changed behavior, not a fixed checklist. These rules apply to all tests and checks.
- Check existing coverage before adding tests. Prefer running or extending existing tests; add new ones only for meaningful coverage gaps or bug regressions.
- Test observable behavior and outcomes, not implementation details. Use the simplest suitable test level; avoid duplicating the same assertion across unit, integration, and browser tests unless each covers a distinct risk.
- Reversible, low-impact changes do not automatically need new tests. For new simple logic, one focused test covering the meaningful cases is usually enough.
- Reuse existing tools. Do not create extra test infrastructure, mock servers, or temporary browser scripts for small changes when existing checks suffice.
- Run the relevant required checks first. Once they pass, stop; broaden or repeat verification only for relevant new changes, failures, or concrete unresolved risks.
- Use visual checks for actual layout, responsive, theme, or interaction changes that need browser validation. Check only affected states; do not automatically run a viewport/theme matrix for every UI edit.
- Keep thorough coverage for data-loss, backup/restore, security, and migration risks. Minimize redundant checks, not necessary safeguards.

## Frontend

- Follow the [Backup Selection UI conventions](CONTRIBUTING.md#backup-selection-ui), including the exceptions for backup types that cannot represent the shared selection behavior faithfully.
- Prefer Quasar utilities for layout, spacing, responsiveness, typography, alignment, visibility, and overflow handling when they are sensible and sufficient.
- Do not add project-specific CSS classes for one-off layout concerns such as dialog width, grid spacing, margins, padding, or text alignment.
- Use custom CSS only for reusable project patterns, such as standardized dialog widths, or when Quasar utilities cannot express the required behavior cleanly.
- Keep dialogs content-driven by default. Avoid `full-width` dialogs unless the content genuinely needs the viewport width.
- Keep tab content transitions instant. Do not use `animated` on `QTabPanels` or add custom animations for tab changes.
