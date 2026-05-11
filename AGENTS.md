# Agent Guidelines

## Git And Releases

- Use Conventional Commit messages because releases are derived from commit messages.
- Use `feat:` for release-relevant new functionality.
- Use `fix:`, `perf:`, `security:`, or `deps:` for release-relevant patch changes.
- Use `docs:`, `chore:`, `test:`, `refactor:`, `style:`, `build:`, or `ci:` for changes that should not trigger a release by themselves.
- Mark intentional breaking changes with `!` in the commit header or a `BREAKING CHANGE:` footer.
- Do not manually bump project versions outside `./scripts/release.sh`.
- Use `chore(release): vX.Y.Z` for release commits.
- `develop` is the integration branch. `main` contains released versions only.
- `docs/changelog/UNRELEASED.md` is kept on `develop` for optional user-facing release notes and is removed from `main` by the release script.

## Frontend

- Prefer Quasar utilities for layout, spacing, responsiveness, typography, alignment, visibility, and overflow handling when they are sensible and sufficient.
- Do not add project-specific CSS classes for one-off layout concerns such as dialog width, grid spacing, margins, padding, or text alignment.
- Use custom CSS only for reusable project patterns, such as standardized dialog widths, or when Quasar utilities cannot express the required behavior cleanly.
- Keep dialogs content-driven by default. Avoid `full-width` dialogs unless the content genuinely needs the viewport width.
