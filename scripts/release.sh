#!/usr/bin/env bash
# Release script: derive the next SemVer release from Conventional Commits,
# merge develop into main, tag the release, and push release refs.

set -euo pipefail

FORCE=false

usage() {
    cat <<'EOF'
Usage:
  ./scripts/release.sh [-f|--force]

Options:
  -f, --force  Continue despite non-conventional commit messages.
               Non-conventional commits are ignored for versioning and changelog generation.
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

if ! git diff --quiet || ! git diff --cached --quiet; then
    abort "Working-Tree ist nicht sauber. Bitte erst committen oder stashen."
fi

CURRENT_BRANCH=$(git symbolic-ref --short HEAD)
if [[ "$CURRENT_BRANCH" != "develop" ]]; then
    abort "Aktueller Branch ist '$CURRENT_BRANCH', erwartet wird 'develop'."
fi

if [[ ! -f "docs/changelog/UNRELEASED.md" ]]; then
    abort "docs/changelog/UNRELEASED.md existiert nicht."
fi

LAST_TAG=$(git describe --tags --abbrev=0 --match 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null || true)
if [[ -z "$LAST_TAG" ]]; then
    abort "Kein SemVer-Tag gefunden. Bitte zuerst einen Baseline-Tag wie v0.1.0 setzen."
fi

NEXT_VERSION=$(
    python3 - "$LAST_TAG" "$FORCE" <<'PY'
import json
import re
import subprocess
import sys
from datetime import date
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
patch_types = {"fix", "perf", "security", "deps"}
minor_types = {"feat"}
other_types = allowed_types - patch_types - minor_types
header_re = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<summary>.+)$")
breaking_re = re.compile(r"^BREAKING[ -]CHANGE: (?P<text>.+)$", re.MULTILINE)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def replace_required(path: str, pattern: str, replacement: str) -> None:
    file_path = Path(path)
    content = file_path.read_text()
    updated, count = re.subn(pattern, replacement, content, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"ABBRUCH: Konnte Version in {path} nicht aktualisieren.")
    file_path.write_text(updated)


tag_match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", last_tag)
if not tag_match:
    raise SystemExit(f"ABBRUCH: Letzter Tag ist kein SemVer-Tag: {last_tag}")

commits = git("rev-list", "--reverse", "--no-merges", f"{last_tag}..HEAD").splitlines()
if not commits:
    raise SystemExit(f"ABBRUCH: Keine Commits seit {last_tag} gefunden.")

entries: list[dict[str, str | bool]] = []
non_conventional: list[str] = []
bump = "none"

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

    if is_breaking:
        bump = "major"
    elif bump != "major" and commit_type in minor_types:
        bump = "minor"
    elif bump not in {"major", "minor"} and commit_type in patch_types:
        bump = "patch"

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
        print("Hinweis: Mit -f werden diese Commits nicht fuer Version und Changelog ausgewertet.", file=sys.stderr)
        sys.exit(2)
    print("WARNUNG: Release wird wegen -f fortgesetzt.", file=sys.stderr)
    print("WARNUNG: Diese Commits werden nicht fuer Version und Changelog ausgewertet.", file=sys.stderr)

if bump == "none":
    raise SystemExit(
        "ABBRUCH: Keine release-relevanten Conventional Commits seit "
        f"{last_tag} gefunden. Erwartet: feat, fix, perf, security, deps oder Breaking Change."
    )

major, minor, patch = (int(part) for part in tag_match.groups())
if bump == "major":
    major += 1
    minor = 0
    patch = 0
elif bump == "minor":
    minor += 1
    patch = 0
else:
    patch += 1

next_version = f"{major}.{minor}.{patch}"
release_date = date.today().isoformat()

Path("VERSION").write_text(f"{next_version}\n")

package_json = Path("apps/frontend/package.json")
package_data = json.loads(package_json.read_text())
package_data["version"] = next_version
package_json.write_text(json.dumps(package_data, indent=2) + "\n")

for pyproject in [
    "apps/backend/pyproject.toml",
    "apps/agent/pyproject.toml",
    "libs/python/common/pyproject.toml",
]:
    replace_required(pyproject, r'^(version\s*=\s*)"[^"]+"', rf'\g<1>"{next_version}"')

lock_packages = {"drastic-agent", "drastic-backup-server", "drastic-common"}
for lock_file in [
    Path("apps/agent/uv.lock"),
    Path("apps/backend/uv.lock"),
    Path("libs/python/common/uv.lock"),
]:
    content = lock_file.read_text()

    def update_lock_entry(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in lock_packages:
            return match.group(0)
        return f'name = "{name}"\nversion = "{next_version}"'

    content = re.sub(
        r'name = "(?P<name>[^"]+)"\nversion = "[^"]+"',
        update_lock_entry,
        content,
    )
    lock_file.write_text(content)

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

release_lines = [f"## [{next_version}] - {release_date}", ""]
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

print(next_version)
PY
)

TAG_NAME="v${NEXT_VERSION}"

echo "=== Release: ${TAG_NAME} ==="
echo "  Letzter Tag: ${LAST_TAG}"
echo "  Naechste Version: ${NEXT_VERSION}"
echo ""

git add \
    VERSION \
    CHANGELOG.md \
    docs/changelog/UNRELEASED.md \
    apps/frontend/package.json \
    apps/backend/pyproject.toml \
    apps/backend/uv.lock \
    apps/agent/pyproject.toml \
    apps/agent/uv.lock \
    libs/python/common/pyproject.toml \
    libs/python/common/uv.lock

git commit -m "chore(release): ${TAG_NAME}"

git switch main
git merge --no-ff develop -m "chore(release): ${TAG_NAME}"

if git ls-files --error-unmatch docs/changelog/UNRELEASED.md >/dev/null 2>&1; then
    git rm -q docs/changelog/UNRELEASED.md
    git commit -m "chore(release): remove unreleased notes from main"
fi

git switch develop
git merge --ff-only main

mkdir -p docs/changelog
printf '# Unreleased\n\n## Release Notes\n' > docs/changelog/UNRELEASED.md
git add docs/changelog/UNRELEASED.md
git commit -m "chore(release): reopen unreleased notes"

git tag "${TAG_NAME}" main
git push origin develop main "${TAG_NAME}"

echo ""
echo "=== Release ${TAG_NAME} erfolgreich abgeschlossen ==="
