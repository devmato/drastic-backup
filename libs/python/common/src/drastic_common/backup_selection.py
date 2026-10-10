"""Resolve literal backup-path exceptions without scanning the filesystem."""

import re
from os.path import abspath, join
from pathlib import PurePosixPath

from drastic_common.truenas import path_is_within


def literal_pattern(path):
    """Escape browser-selected names for Restic's glob-based exclusions."""
    return re.sub(r"([\\*?\[\]])", r"\\\1", str(path))


def restic_path_selection(paths, excluded, cwd=None):
    """Keep explicit sources and apply literal rules from parents to children.

    Restic starts at every explicit source, including children of excluded parents.
    Negated patterns then reopen that child's contents; deeper exclusions still win.
    Preserve source spelling so relative TrueNAS snapshots keep their restore layout.
    Callers append free-form exclude patterns after these literal rules.
    """
    def absolute(path):
        return PurePosixPath(abspath(join(str(cwd or ""), str(path))))

    exclusions = {absolute(path) for path in excluded}
    sources = [path for path in paths if absolute(path) not in exclusions]
    rules = {path: True for path in exclusions}
    for source in sources:
        path = absolute(source)
        if any(path.is_relative_to(parent) for parent in exclusions):
            rules[path] = False
    patterns = [("" if exclude else "!") + literal_pattern(path)
                for path, exclude in sorted(rules.items(), key=lambda item: (len(item[0].parts), str(item[0])))]
    return sources, patterns


def requires_include_exceptions(job_type, config):
    """Identify configurations older agents cannot execute faithfully."""
    config = config or {}
    if job_type == "truenas":
        return any(
            (entry["dataset"], entry["path"]) != (parent["dataset"], parent["path"])
            and (path_is_within(entry, parent)
                 # Custom mountpoints are only known to the agent. A child dataset
                 # may also sit inside an excluded folder of its parent dataset.
                 or (parent["group"] != "file" and entry["dataset"].startswith(parent["dataset"] + "/")))
            for entry in config.get("paths", []) for parent in config.get("exclude_paths", []))
    if job_type == "file":
        return any(PurePosixPath(abspath(entry["path"])) != PurePosixPath(abspath(parent["path"]))
                   and PurePosixPath(abspath(entry["path"])).is_relative_to(abspath(parent["path"]))
                   for entry in config.get("paths", []) for parent in config.get("exclude_patterns", [])
                   if parent.get("group") in {"folder", "file"})
    return False
