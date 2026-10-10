import os
import subprocess
import sys
from pathlib import Path

import pytest

from drastic_common import version


def test_version_survives_packaging_and_marks_local_changes(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.net")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.net")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-08T00:30:00+02:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-08T00:30:00+02:00")
    git("init", "-b", "main")
    (tmp_path / "source.py").write_text("original\n")
    (tmp_path / ".gitignore").write_text("build-version.txt\nbuild-revision.txt\n")
    git("add", ".")
    git("commit", "-m", "test: version")
    expected = f"2026-10-07-{git('rev-parse', 'HEAD')[:8]}"
    revision = git("rev-parse", "HEAD")
    assert version.source_revision(tmp_path) == revision
    assert version.source_version(tmp_path) == expected
    # Local timezone and branch names do not affect the version.
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    git("switch", "-c", "develop")
    assert version.source_version(tmp_path) == expected

    script = Path(version.__file__)
    stamp = tmp_path / "build-version.txt"
    revision_stamp = tmp_path / "build-revision.txt"
    subprocess.run([sys.executable, str(script), "--source", str(tmp_path), "--output", str(stamp),
                    "--revision-output", str(revision_stamp)], check=True)
    assert stamp.read_text().strip() == expected
    assert revision_stamp.read_text().strip() == revision
    (tmp_path / "source.py").write_text("changed\n")
    assert version.source_version(tmp_path) == expected + "-dirty"
    git("add", "source.py")
    assert version.source_version(tmp_path) == expected + "-dirty"
    git("reset", "--hard", "HEAD")
    (tmp_path / "untracked.py").touch()
    assert version.source_version(tmp_path) == expected + "-dirty"

    # An installed package uses its stamp even without Git or a checkout.
    package = tmp_path / ".venv/lib/python3.11/site-packages/drastic_common"
    package.mkdir(parents=True)
    (package / "version.py").write_bytes(script.read_bytes())
    (package / "build-version.txt").write_bytes(stamp.read_bytes())
    (package / "build-revision.txt").write_bytes(revision_stamp.read_bytes())
    env = {**os.environ, "PATH": "", "PYTHONPATH": str(package)}
    command = [sys.executable, "-c", "from version import get_version; print(get_version())"]
    assert subprocess.check_output(command, cwd=package, env=env, text=True).strip() == expected
    revision_command = [sys.executable, "-c", "from version import get_revision; print(get_revision())"]
    assert subprocess.check_output(revision_command, cwd=package, env=env, text=True).strip() == revision
    (package / "build-revision.txt").write_text("invalid\n")
    assert subprocess.check_output(revision_command, cwd=package, env=env, text=True).strip() == "None"
    (package / "build-version.txt").unlink()
    # A venv inside another checkout must not report that checkout's version.
    env["PATH"] = os.environ["PATH"]
    assert subprocess.check_output(command, cwd=package, env=env, text=True).strip() == "unknown"


@pytest.mark.skipif(os.environ.get("DRASTIC_TEST_DOCKER") != "1", reason="Set DRASTIC_TEST_DOCKER=1 to run Docker builds")
def test_docker_versions_from_checkouts_and_linked_worktrees(tmp_path, monkeypatch):
    project = Path(__file__).resolve().parents[4]
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.net")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.net")

    def run(*args):
        return subprocess.check_output(args, cwd=repo, text=True, stderr=subprocess.STDOUT).strip()

    dockerfiles = ("Dockerfile", "apps/agent/Dockerfile")
    helper = "libs/python/common/src/drastic_common/version.py"
    for name in (*dockerfiles, helper):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((project / name).read_bytes())
    run("git", "init", "-b", "main")
    run("git", "add", ".")
    run("git", "commit", "-m", "test: Docker version")
    worktree = tmp_path / "worktree"
    run("git", "worktree", "add", "--detach", str(worktree))
    assert (worktree / ".git").is_file()
    expected = version.source_version(repo)

    for index, dockerfile in enumerate(dockerfiles):
        image = f"drastic-worktree-version-test-{os.getpid()}-{index}"
        try:
            for context, dirty in ((repo, False), (worktree, False), (worktree, True)):
                extra_args = []
                if dirty:
                    (worktree / "local-change").touch()
                if context == worktree:
                    value = run(sys.executable, str(worktree / helper), "--source", str(worktree))
                    assert value == expected + ("-dirty" if dirty else "")
                    revision = run("git", "rev-parse", "HEAD")
                    extra_args = ["--build-arg", f"DRASTIC_VERSION={value}", "--build-arg", f"DRASTIC_REVISION={revision}"]
                run("docker", "build", "--target", "version-build", "-t", image,
                    "-f", str(context / dockerfile), *extra_args, str(context))
                actual = run("docker", "run", "--rm", image, "python", "-c",
                             "from pathlib import Path; print(Path('/build-version.txt').read_text().strip())")
                assert actual == expected + ("-dirty" if dirty else "")
                actual_revision = run("docker", "run", "--rm", image, "python", "-c",
                                      "from pathlib import Path; print(Path('/build-revision.txt').read_text().strip())")
                assert actual_revision == run("git", "rev-parse", "HEAD")
            (worktree / "local-change").unlink()
        finally:
            subprocess.run(["docker", "image", "rm", image], capture_output=True)
