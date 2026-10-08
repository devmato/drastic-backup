import json
import os
import re
import subprocess
import sys
from pathlib import Path


def test_release_tags_final_main_commit_and_migrates_legacy_tags(tmp_path):
    project = Path(__file__).resolve().parents[4]
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.net",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.net"}
    # Exercise publishing without contacting a registry or running Docker.
    docker = tmp_path / "docker"
    calls = tmp_path / "docker-calls.jsonl"
    docker.write_text(f"#!{sys.executable}\nimport json, sys\n"
                      f"with open({str(calls)!r}, 'a') as log: log.write(json.dumps(sys.argv[1:]) + '\\n')\n")
    docker.chmod(0o755)
    env.update(PATH=f"{tmp_path}:{env['PATH']}", CONTAINER_REGISTRY="registry.test",
               CONTAINER_USERNAME="test", CONTAINER_PASSWORD="test", GITHUB_REPOSITORY="owner/repo",
               CONTAINER_IMAGE_NAMESPACE="owner/repo", CONTAINER_PLATFORMS="")

    def run(*args):
        return subprocess.check_output(args, cwd=repo, env=env, text=True, stderr=subprocess.STDOUT).strip()

    run("git", "init", "--bare", str(tmp_path / "remote.git"))
    run("git", "init", "-b", "main")
    run("git", "remote", "add", "origin", str(tmp_path / "remote.git"))
    for name in ("scripts/release.sh", "libs/python/common/src/drastic_common/version.py"):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((project / name).read_bytes())
    (repo / "CHANGELOG.md").write_text("# Changelog\n")
    run("git", "add", ".")
    run("git", "commit", "-m", "chore: baseline")
    run("git", "tag", "v0.1.0")
    run("git", "switch", "-c", "develop")
    notes = repo / "docs/changelog/UNRELEASED.md"
    notes.parent.mkdir(parents=True)

    for index in range(2):
        notes.write_text(f"# Unreleased\n\n## Release Notes\n\nRelease note {index}\n")
        run("git", "add", ".")
        run("git", "commit", "-m", f"feat: change {index}")
        run("bash", "scripts/release.sh")
        tag = run("git", "describe", "--tags", "--exact-match", "main")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{8}", tag)
        assert tag.endswith(run("git", "rev-parse", "main")[:8])
        assert run("git", "branch", "--show-current") == "develop"
        assert run("git", "status", "--porcelain") == ""
        assert "UNRELEASED.md" not in run("git", "ls-tree", "-r", "--name-only", "main")
        assert notes.read_text() == "# Unreleased\n\n## Release Notes\n"
        assert f"Release note {index}" in run("git", "show", "main:CHANGELOG.md")
        run("git", "switch", "--detach", tag)
        assert run("python3", "libs/python/common/src/drastic_common/version.py") == tag
        assert run("git", "ls-remote", "origin", f"refs/tags/{tag}").split()[0] == run("git", "rev-parse", "HEAD")
        env.update(GITHUB_REF_TYPE="tag", GITHUB_REF_NAME=tag, GITHUB_SHA=run("git", "rev-parse", "HEAD"))
        run("bash", str(project / "scripts/ci/publish-docker-image.sh"), "agent", "apps/agent/Dockerfile", ".")
        commands = [json.loads(line) for line in calls.read_text().splitlines()]
        assert ["push", f"registry.test/owner/repo/agent:{tag}"] in commands
        assert ["push", "registry.test/owner/repo/agent:latest"] in commands
        build = next(command for command in reversed(commands) if command[0] == "build")
        assert "DRASTIC_VERSION=" + tag in build
        assert "DRASTIC_AGENT_REF=main" in build
        assert "DRASTIC_AGENT_COMMIT=" + env["GITHUB_SHA"] in build
        env["CONTAINER_PLATFORMS"] = "linux/amd64,linux/arm64"
        run("bash", str(project / "scripts/ci/publish-docker-image.sh"), "server", "Dockerfile", ".")
        buildx = json.loads(calls.read_text().splitlines()[-1])
        assert buildx[:2] == ["buildx", "build"]
        assert "DRASTIC_VERSION=" + tag in buildx
        env["CONTAINER_PLATFORMS"] = ""
        run("git", "switch", "develop")
