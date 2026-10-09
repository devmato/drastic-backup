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

## Architecture

- Follow [Architecture](docs/architecture.md) and keep [Project Structure](docs/project-structure.md) accurate when moving responsibilities or entry points.
- Keep the backend, agent, frontend and common library as separate application boundaries. `drastic_common` must not import either application.
- Organize services by business responsibility. Prefer functions; use classes for real state or a cohesive set of dependencies. Split modules by responsibility, not a fixed line count.
- Backend HTTP and Socket.IO handlers adapt validated inputs, authentication and responses. Application services own workflows, resource ownership checks and commit points. Do not import request state, call `abort`, or use `first_or_404` inside services; pass identity and request metadata explicitly.
- Use the existing SQLAlchemy models directly. Extract recurring complex queries near their feature; do not add generic repositories, service base classes, dependency-injection containers or event buses.
- Commit durable state before sending commands or publishing updates. The HTTP error boundary rolls back unfinished transactions; background/CLI callers must also roll back failed work. Document intentional intermediate commits and recovery behavior.
- Keep backend agent-command transport in `integrations/agent.py`, operation lifecycle/report handling in `services/operations/`, and job workflows in `services/jobs/`.
- The agent composition root is `runtime/agent.py`. Put scheduling, command admission, reporting, updates and transport in their respective runtime modules; local persistence belongs in `storage/`, business workflows in `services/` and backup handlers in `jobs/`.
- Pass explicit capabilities to workers (`BackupContext`, `RestoreContext`) and focused dependencies to runtime helpers. Do not turn the whole Agent object into a service locator or split it using mixins.
- Preserve operation UUID deduplication, durable schedule claims, admission/cancellation fences, report replay, integrity-check retention blocks and recoverable repository deletion when refactoring. A dispatch timeout is an unknown outcome, not proof that execution failed.
- Frontend endpoint calls belong in `src/api/`; stores own shared state and refreshes, composables own multi-step reactive workflows, and pages/components own presentation. API modules must not import stores or display dialogs/notifications. Existing stateless store entry points may delegate to API modules.
- Preserve the external API, agent protocol and on-disk formats during structural refactors. Changes to these contracts need explicit migration/compatibility work and focused tests.

## Comments And Readability

- Write code comments and docstrings in English, consistent with identifiers and existing documentation.
- Give responsibility-bearing modules a short purpose docstring. Document non-obvious public service contracts, side effects, transaction boundaries and failure behavior; do not repeat obvious signatures.
- Explain why code exists, especially ordering, concurrency, retries, recovery, protocol restrictions and data-protection rules. Keep the explanation next to the relevant code.
- Prefer descriptive names and readable control flow over compressed expressions. Do not impose comment quotas or comment every statement.
- Remove commented-out code when touching its area. Update comments alongside behavior changes; use actionable TODOs with a reason rather than vague reminders.

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
