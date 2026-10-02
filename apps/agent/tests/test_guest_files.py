import os
import stat
from pathlib import Path

import pytest

from drastic_agent.services.guest_files import export_files, guest_path


def test_guest_paths_have_one_leading_slash():
    assert guest_path("//") == "/"
    assert guest_path("//etc//hostname") == "/etc/hostname"


class Guest:
    """Filesystem-backed stand-in for the read-only guestfs calls used by export."""
    def __init__(self, root):
        self.root = root

    def path(self, path):
        return self.root / path.lstrip("/")

    def ls(self, path):
        return [p.name for p in self.path(path).iterdir()]

    def lstatns(self, path):
        info = self.path(path).lstat()
        return {"st_mode": info.st_mode, "st_size": info.st_size, "st_atime_sec": int(info.st_atime),
                "st_atime_nsec": 0, "st_mtime_sec": int(info.st_mtime), "st_mtime_nsec": 0}

    def readlink(self, path):
        return os.readlink(self.path(path))

    def download(self, source, target):
        Path(target).write_bytes(self.path(source).read_bytes())


def test_guest_export_preserves_contents_links_and_refuses_collisions(tmp_path):
    source = tmp_path / "guest"
    source.mkdir()
    (source / "etc").mkdir()
    (source / "etc" / "hello.txt").write_text("Linux / Windows UTF-8: ä\n")
    (source / "etc" / "hello.txt").chmod(0o4755)
    (source / "etc" / "link").symlink_to("/etc/passwd")
    os.mkfifo(source / "etc" / "pipe")
    request = {"target": str(tmp_path / "out"), "paths": ["/etc", "/etc/hello.txt"]}
    result = export_files(Guest(source), request)
    target = tmp_path / "out" / "etc" / "hello.txt"
    assert target.read_bytes() == (source / "etc" / "hello.txt").read_bytes()
    assert not target.stat().st_mode & stat.S_ISUID
    assert os.readlink(tmp_path / "out" / "etc" / "link") == "/etc/passwd"
    assert result["files"] == 2 and result["skipped_special_files"] == 1
    with pytest.raises(ValueError, match="already exists"):
        export_files(Guest(source), request)
    (source / "etc" / "hello.txt").write_text("updated")
    export_files(Guest(source), {**request, "overwrite_policy": "overwrite"})
    assert target.read_text() == "updated"


@pytest.mark.parametrize("policy", ["fail_if_exists", "overwrite"])
def test_guest_export_never_follows_host_or_guest_symlink_parents(tmp_path, policy):
    source = tmp_path / "guest"
    source.mkdir()
    (source / "etc").mkdir()
    (source / "etc" / "secret").write_text("guest data")
    (source / "alias").symlink_to(source / "etc", target_is_directory=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("keep me")
    target = tmp_path / "out"
    target.mkdir()
    (target / "etc").symlink_to(outside, target_is_directory=True)
    request = {"target": str(target), "paths": ["/etc/secret"], "overwrite_policy": policy}
    with pytest.raises(OSError):
        export_files(Guest(source), request)
    with pytest.raises(ValueError, match="non-directory"):
        export_files(Guest(source), {**request, "paths": ["/alias/secret"]})
    assert (outside / "secret").read_text() == "keep me"


def test_overwrite_replaces_link_without_writing_through_it(tmp_path):
    source = tmp_path / "guest"
    source.mkdir()
    (source / "file").write_text("new")
    target = tmp_path / "out"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("original")
    (target / "file").symlink_to(outside)
    export_files(Guest(source), {"target": str(target), "paths": ["/file"], "overwrite_policy": "overwrite"})
    assert outside.read_text() == "original"
    assert (target / "file").read_text() == "new"
    assert not (target / "file").is_symlink()


@pytest.mark.parametrize("path", ["../escape", "/a/../escape", "/bad\x00path"])
def test_invalid_guest_selection_rejected_before_writing(tmp_path, path):
    with pytest.raises(ValueError):
        export_files(Guest(tmp_path), {"target": str(tmp_path / "out"), "paths": [path]})
    assert not (tmp_path / "out").exists()
