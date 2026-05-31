from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def agent_version() -> str:
    try:
        return version("drastic-agent")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if pyproject_path.is_file():
            pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            project_version = str(pyproject.get("project", {}).get("version") or "").strip()
            if project_version:
                return project_version

    return "unknown"
