#!/usr/bin/env bash
# Generate release notes, merge develop into main, and tag its final source version.

set -euo pipefail

FORCE=false

usage() {
    cat <<'EOF'
Usage:
  ./scripts/release.sh [-f|--force]

Options:
  -f, --force  Continue despite non-conventional commit messages.
               Non-conventional commits are ignored for changelog generation.
  -h, --help   Show this help.
EOF
}

abort() {
    echo "ABBRUCH: $1" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--force)
            FORCE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            abort "Unbekannte Option: $1"
            ;;
    esac
done

if [[ -n "$(git status --porcelain)" ]]; then
    abort "Working-Tree ist nicht sauber. Bitte erst committen oder stashen."
fi

CURRENT_BRANCH=$(git symbolic-ref --short HEAD)
if [[ "$CURRENT_BRANCH" != "develop" ]]; then
    abort "Aktueller Branch ist '$CURRENT_BRANCH', erwartet wird 'develop'."
fi

if [[ ! -f "docs/changelog/UNRELEASED.md" ]]; then
    abort "docs/changelog/UNRELEASED.md existiert nicht."
fi

LAST_TAG=$(git describe --tags --abbrev=0 --match '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-????????' --match 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null || true)
if [[ -z "$LAST_TAG" ]]; then
    abort "Kein Release-Tag gefunden. Bitte zuerst einen Baseline-Tag setzen."
fi

python3 - "$LAST_TAG" "$FORCE" <<'PY'
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

last_tag = sys.argv[1]
force = sys.argv[2].lower() == "true"

allowed_types = {
    "feat",
    "fix",
    "perf",
    "security",
    "deps",
    "docs",
    "chore",
    "test",
    "refactor",
    "style",
    "build",
    "ci",
    "revert",
}
release_types = {"feat", "fix", "perf", "security", "deps"}
other_types = allowed_types - release_types
header_re = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<summary>.+)$")
breaking_re = re.compile(r"^BREAKING[ -]CHANGE: (?P<text>.+)$", re.MULTILINE)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


commits = git("rev-list", "--reverse", "--no-merges", f"{last_tag}..HEAD").splitlines()
if not commits:
    raise SystemExit(f"ABBRUCH: Keine Commits seit {last_tag} gefunden.")

entries: list[dict[str, str | bool]] = []
non_conventional: list[str] = []
release_relevant = False

for sha in commits:
    subject = git("log", "-1", "--format=%s", sha).strip()
    body = git("log", "-1", "--format=%b", sha)
    if subject.startswith("chore(release):"):
        continue

    match = header_re.match(subject)

    if not match or match.group("type") not in allowed_types:
        non_conventional.append(f"{sha[:7]} {subject}")
        continue

    commit_type = match.group("type")
    scope = match.group("scope") or ""
    summary = match.group("summary").strip()
    breaking_match = breaking_re.search(body)
    is_breaking = bool(match.group("breaking") or breaking_match)
    breaking_text = breaking_match.group("text").strip() if breaking_match else summary

    if is_breaking or commit_type in release_types:
        release_relevant = True

    entries.append(
        {
            "sha": sha[:7],
            "type": commit_type,
            "scope": scope,
            "summary": summary,
            "breaking": is_breaking,
            "breaking_text": breaking_text,
        }
    )

if non_conventional:
    print("ABBRUCH: Nicht-konforme Commit Messages gefunden.", file=sys.stderr)
    print("", file=sys.stderr)
    for item in non_conventional:
        print(f"- {item}", file=sys.stderr)
    print("", file=sys.stderr)
    if not force:
        print("Bitte Commit Messages korrigieren oder bewusst mit -f releasen.", file=sys.stderr)
        print("Hinweis: Mit -f werden diese Commits nicht fuer das Changelog ausgewertet.", file=sys.stderr)
        sys.exit(2)
    print("WARNUNG: Release wird wegen -f fortgesetzt.", file=sys.stderr)
    print("WARNUNG: Diese Commits werden nicht fuer das Changelog ausgewertet.", file=sys.stderr)

if not release_relevant:
    raise SystemExit(
        "ABBRUCH: Keine release-relevanten Conventional Commits seit "
        f"{last_tag} gefunden. Erwartet: feat, fix, perf, security, deps oder Breaking Change."
    )

release_date = datetime.now(timezone.utc).date().isoformat()

manual_notes_path = Path("docs/changelog/UNRELEASED.md")
manual_lines = []
for line in manual_notes_path.read_text().splitlines():
    stripped = line.strip()
    if not stripped or stripped in {"# Unreleased", "## Release Notes"}:
        continue
    manual_lines.append(line)

sections = [
    ("Breaking Changes", [entry for entry in entries if entry["breaking"]]),
    ("Features", [entry for entry in entries if entry["type"] == "feat" and not entry["breaking"]]),
    ("Bug Fixes", [entry for entry in entries if entry["type"] == "fix" and not entry["breaking"]]),
    ("Performance", [entry for entry in entries if entry["type"] == "perf" and not entry["breaking"]]),
    ("Security", [entry for entry in entries if entry["type"] == "security" and not entry["breaking"]]),
    ("Dependencies", [entry for entry in entries if entry["type"] == "deps" and not entry["breaking"]]),
    ("Other Changes", [entry for entry in entries if entry["type"] in other_types and not entry["breaking"]]),
]

# The final commit hash cannot be embedded in its own committed changelog.
release_lines = [f"## Release - {release_date}", ""]
if manual_lines:
    release_lines.extend(["### Release Notes", "", *manual_lines, ""])

for title, section_entries in sections:
    if not section_entries:
        continue
    release_lines.extend([f"### {title}", ""])
    for entry in section_entries:
        scope = f"{entry['scope']}: " if entry["scope"] else ""
        summary = entry["breaking_text"] if entry["breaking"] else entry["summary"]
        release_lines.append(f"- {scope}{summary} ({entry['sha']})")
    release_lines.append("")

changelog = Path("CHANGELOG.md")
current_changelog = changelog.read_text().rstrip()
changelog.write_text(f"{current_changelog}\n\n" + "\n".join(release_lines).rstrip() + "\n")

manual_notes_path.write_text("# Unreleased\n\n## Release Notes\n")

PY

git add CHANGELOG.md docs/changelog/UNRELEASED.md
git commit -m "chore(release): prepare release notes"

git switch main
git merge --no-ff develop -m "chore(release): merge develop into main"

if git ls-files --error-unmatch docs/changelog/UNRELEASED.md >/dev/null 2>&1; then
    git rm -q docs/changelog/UNRELEASED.md
    git commit -m "chore(release): remove unreleased notes from main"
fi

TAG_NAME=$(python3 libs/python/common/src/drastic_common/version.py)
git tag "${TAG_NAME}" main

git switch develop
git merge --ff-only main

mkdir -p docs/changelog
printf '# Unreleased\n\n## Release Notes\n' > docs/changelog/UNRELEASED.md
git add docs/changelog/UNRELEASED.md
git commit -m "chore(release): reopen unreleased notes"

git push origin develop main "${TAG_NAME}"

echo ""
echo "=== Release ${TAG_NAME} erfolgreich abgeschlossen ==="
