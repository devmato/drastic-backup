#!/usr/bin/env bash
# Validate and tag the current source commit locally; print release notes to stdout.
set -euo pipefail

if [[ $# -gt 1 || "${1:-}" == -* ]]; then
    echo "Usage: ./scripts/release.sh [previous-published-tag]" >&2
    exit 1
fi
BASE_TAG=${1:-}
if [[ -n "$(git status --porcelain)" ]]; then
    echo "Release preparation requires a clean working tree." >&2
    exit 1
fi
VERSION=$(python3 libs/python/common/src/drastic_common/version.py)
if [[ -n "$BASE_TAG" ]]; then
    git merge-base --is-ancestor "refs/tags/$BASE_TAG^{commit}" HEAD
fi
if git show-ref --verify --quiet "refs/tags/$VERSION"; then
    test "$(git rev-parse "refs/tags/$VERSION^{commit}")" = "$(git rev-parse HEAD)"
fi

python3 - "$BASE_TAG" "$VERSION" <<'PY'
import os
import re
import subprocess
import sys
from pathlib import Path

base, version = sys.argv[1:]
url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', 'devmato/drastic-backup')}"
sections = {name: [] for name in (
    'Breaking Changes', 'Features', 'Bug Fixes', 'Performance', 'Security', 'Dependencies', 'Other Changes',
)}
types = {'feat': 'Features', 'fix': 'Bug Fixes', 'perf': 'Performance',
         'security': 'Security', 'deps': 'Dependencies'}
allowed = set(types) | {'docs', 'chore', 'test', 'refactor', 'style', 'build', 'ci', 'revert'}
header = re.compile(r'^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<summary>.+)$')


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


commits = git('rev-list', '--reverse', '--no-merges', f'{base}..HEAD' if base else 'HEAD').splitlines()
if not commits:
    raise SystemExit('No changes since the previous published release.')
for sha in commits:
    subject = git('log', '-1', '--format=%s', sha)
    match = header.fullmatch(subject)
    if not match or match['type'] not in allowed:
        raise SystemExit(f'Non-conventional commit: {sha[:8]} {subject}')
    body = git('log', '-1', '--format=%b', sha)
    breaking = re.search(r'^BREAKING[ -]CHANGE: (.+)', body, re.MULTILINE | re.DOTALL)
    section = 'Breaking Changes' if match['breaking'] or breaking else types.get(match['type'], 'Other Changes')
    scope = f"{match['scope']}: " if match['scope'] else ''
    entry = f"- {scope}{match['summary']} ([{sha[:8]}]({url}/commit/{sha}))"
    if breaking:
        entry += '\n\n' + '\n'.join('  ' + line for line in breaking[1].splitlines())
    sections[section].append(entry)

# Preserve the existing pre-release notes once, without an ongoing changelog file.
initial = Path('docs/initial-release-notes.md')
if not base and initial.is_file():
    print(initial.read_text().rstrip(), '\n')
for title, entries in sections.items():
    if entries:
        print(f'## {title}\n\n' + '\n'.join(entries) + '\n')
if base:
    print(f'[Full changelog]({url}/compare/{base}...{version})')
else:
    print(f'[Full history]({url}/commits/{version})')
PY

if ! git show-ref --verify --quiet "refs/tags/$VERSION"; then
    git tag "$VERSION" HEAD
fi
