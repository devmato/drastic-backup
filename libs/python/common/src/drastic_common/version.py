"""One source version for every deployment; stamp it before packaging."""

import argparse
import subprocess
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

SOURCE_ROOT = (Path(__file__).resolve().parent / "../../../../..").resolve()


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
    stamp = Path(__file__).with_name("build-version.txt")
    if stamp.is_file():
        return stamp.read_text().strip()
    if Path(__file__).resolve() != SOURCE_ROOT / "libs/python/common/src/drastic_common/version.py":
        return "unknown"
    try:
        return source_version(SOURCE_ROOT)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    value = source_version(args.source)
    if args.output:
        args.output.write_text(value + "\n")
    else:
        print(value)
