"""One source version for every deployment; stamp it before packaging."""

import argparse
import re
import subprocess
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

SOURCE_ROOT = (Path(__file__).resolve().parent / "../../../../..").resolve()


def source_revision(source: Path) -> str:
    source = source.resolve()
    if not (source / ".git").exists():
        raise FileNotFoundError(f"No Git checkout at {source}")
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={source}", "-C", str(source), "rev-parse", "HEAD"],
        text=True, stderr=subprocess.PIPE,
    ).strip()


def source_version(source: Path) -> str:
    source = source.resolve()
    if not (source / ".git").exists():
        raise FileNotFoundError(f"No Git checkout at {source}")
    git = ["git", "-c", f"safe.directory={source}", "-C", str(source)]
    timestamp, commit = subprocess.check_output(
        [*git, "show", "-s", "--format=%ct %H", "HEAD"], text=True, stderr=subprocess.PIPE,
    ).split()
    date = datetime.fromtimestamp(int(timestamp), timezone.utc).date()
    dirty = subprocess.check_output(
        [*git, "status", "--porcelain", "--untracked-files=normal"], text=True, stderr=subprocess.PIPE,
    ).strip()
    return f"{date}-{commit[:8]}" + ("-dirty" if dirty else "")


@cache
def get_version() -> str:
    # A checkout must report local edits even if an old packaging stamp remains.
    if Path(__file__).resolve() == SOURCE_ROOT / "libs/python/common/src/drastic_common/version.py" and (SOURCE_ROOT / ".git").exists():
        try:
            return source_version(SOURCE_ROOT)
        except (OSError, subprocess.CalledProcessError):
            return "unknown"
    stamp = Path(__file__).with_name("build-version.txt")
    if stamp.is_file():
        return stamp.read_text().strip()
    return "unknown"


@cache
def get_revision() -> str | None:
    """Return the full source commit, including in packages without Git metadata."""
    try:
        if Path(__file__).resolve() == SOURCE_ROOT / "libs/python/common/src/drastic_common/version.py" and (SOURCE_ROOT / ".git").exists():
            value = source_revision(SOURCE_ROOT)
        else:
            value = Path(__file__).with_name("build-revision.txt").read_text().strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--revision-output", type=Path)
    args = parser.parse_args()
    value = source_version(args.source)
    if args.output:
        args.output.write_text(value + "\n")
    else:
        print(value)
    if args.revision_output:
        args.revision_output.write_text(source_revision(args.source) + "\n")
