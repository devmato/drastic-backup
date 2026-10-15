import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT = Path(__file__).resolve().parents[4]
PREPARE = yaml.safe_load((PROJECT / '.github/workflows/release.yml').read_text())['jobs']
PUBLISH = yaml.safe_load((PROJECT / '.github/workflows/publish-release.yml').read_text())['jobs']


@pytest.fixture
def release_repo(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.net',
           'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.net',
           'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_REF_TYPE': 'branch', 'GITHUB_REF_NAME': 'develop',
           'GITHUB_OUTPUT': str(tmp_path / 'output'), 'RUNNER_TEMP': str(tmp_path),
           'CONTAINER_REGISTRY': 'registry.test', 'CONTAINER_USERNAME': 'test', 'CONTAINER_PASSWORD': 'test',
           'CONTAINER_PLATFORMS': '', 'PATH': f"{tmp_path}:{os.environ['PATH']}"}
    calls = tmp_path / 'docker-calls.jsonl'
    calls.write_text('')
    docker = tmp_path / 'docker'
    docker.write_text(f'#!{sys.executable}\nimport json, os, sys\n'
                      f"with open({str(calls)!r}, 'a') as log: log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                      "if os.environ.get('FAIL_DOCKER_ARG') in sys.argv[1:]:\n"
                      "    print('registry unavailable'); sys.exit(1)\n"
                      "if sys.argv[1:4] == ['buildx', 'imagetools', 'inspect'] and os.environ.get('IMAGE_EXISTS') != '1':\n"
                      "    print('manifest unknown'); sys.exit(1)\n")
    docker.chmod(0o755)
    releases = tmp_path / 'releases.json'
    releases.write_text('[]')
    gh = tmp_path / 'gh'
    gh.write_text(f'#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n'
                  f'data = Path({str(releases)!r})\n'
                  "if sys.argv[1] == 'api':\n"
                  "    if any('/actions/workflows/' in arg for arg in sys.argv):\n"
                  "        print(json.dumps([{'workflow_runs': json.loads(os.environ.get('PREPARATION_RUNS', '[]'))}]))\n"
                  "    else: print(json.dumps([json.loads(data.read_text())]))\n"
                  "elif sys.argv[1:3] == ['release', 'create']:\n"
                  "    assert '--draft' in sys.argv and '--verify-tag' in sys.argv\n"
                  "    body = Path(sys.argv[sys.argv.index('--notes-file') + 1]).read_text()\n"
                  "    entries = json.loads(data.read_text())\n"
                  "    assert not any(item['tag_name'] == sys.argv[3] for item in entries)\n"
                  "    entries.append({'id': 1, 'tag_name': sys.argv[3], 'draft': True, 'prerelease': False, 'body': body})\n"
                  "    data.write_text(json.dumps(entries))\n"
                  "else: sys.exit(1)\n")
    gh.chmod(0o755)

    def run(*args):
        return subprocess.check_output(args, cwd=repo, env=env, text=True, stderr=subprocess.STDOUT).strip()

    run('git', 'init', '--bare', str(tmp_path / 'remote.git'))
    run('git', 'init', '-b', 'develop')
    run('git', 'remote', 'add', 'origin', str(tmp_path / 'remote.git'))
    for name in ('scripts/release.sh', 'scripts/ci/publish-docker-image.sh', 'scripts/ci/promote-release-images.sh',
                 'libs/python/common/src/drastic_common/version.py'):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((PROJECT / name).read_bytes())
    initial = repo / 'docs/initial-release-notes.md'
    initial.parent.mkdir()
    initial.write_text('Initial manual notes\n')
    run('git', 'add', '.')
    run('git', 'commit', '-m', 'chore: baseline')
    run('git', 'push', 'origin', 'develop')
    env['TAG'] = run('python3', 'libs/python/common/src/drastic_common/version.py')
    return repo, env, run, calls, releases


def test_preparation_keeps_source_and_existing_draft_notes(release_repo, tmp_path):
    repo, env, run, _, releases = release_repo
    source = run('git', 'rev-parse', 'HEAD')
    branches = run('git', 'show-ref', '--heads')
    remote = run('git', 'ls-remote', 'origin')
    prepare = next(step['run'] for step in PREPARE['tag']['steps'] if 'run' in step)
    draft = next(step['run'] for step in PREPARE['draft']['steps'] if 'run' in step)
    run('bash', '-e', '-o', 'pipefail', '-c', prepare)
    env['BASE_TAG'] = ''
    assert run('git', 'rev-parse', f"refs/tags/{env['TAG']}") == source
    assert run('git', 'show-ref', '--heads') == branches
    assert run('git', 'status', '--porcelain') == ''
    assert run('git', 'ls-remote', '--heads', 'origin') == remote
    run('bash', '-e', '-o', 'pipefail', '-c', draft)
    saved = json.loads(releases.read_text())
    assert saved[0]['draft'] is True
    assert 'Initial manual notes' in saved[0]['body']
    saved[0]['body'] = 'Manually edited migration instructions'
    releases.write_text(json.dumps(saved))
    (repo / 'new-work.txt').write_text('Later development\n')
    run('git', 'add', '.')
    run('git', 'commit', '-m', 'fix: later work')
    run('git', 'push', 'origin', 'develop')
    run('git', 'checkout', '--detach', source)
    for published in (False, True):
        saved[0]['draft'] = not published
        releases.write_text(json.dumps(saved))
        run('bash', '-e', '-o', 'pipefail', '-c', prepare)
        run('bash', '-e', '-o', 'pipefail', '-c', draft)
        assert json.loads(releases.read_text()) == saved
        assert run('git', 'rev-parse', 'HEAD') == source


def test_notes_use_published_base_not_abandoned_tags(release_repo, tmp_path):
    repo, env, run, _, releases = release_repo
    base = env['TAG']
    run('git', 'tag', base)
    (repo / 'change.txt').write_text('First change\n')
    run('git', 'add', '.')
    run('git', 'commit', '-m', 'feat(backup): retry transport failures')
    abandoned = run('python3', 'libs/python/common/src/drastic_common/version.py')
    run('git', 'tag', abandoned)
    (repo / 'change.txt').write_text('Second change\n')
    run('git', 'commit', '-am', 'fix(agent)!: require new protocol', '-m',
        'BREAKING CHANGE: Update the backend first.\nThen update all agents.')
    run('git', 'push', 'origin', 'develop')
    releases.write_text(json.dumps([
        {'tag_name': base, 'draft': False, 'prerelease': False, 'published_at': '2026-01-01'},
        {'tag_name': abandoned, 'draft': True, 'prerelease': False, 'published_at': None},
        {'tag_name': abandoned, 'draft': False, 'prerelease': True, 'published_at': '2026-02-01'},
    ]))
    prepare = next(step['run'] for step in PREPARE['tag']['steps'] if 'run' in step)
    run('bash', '-e', '-o', 'pipefail', '-c', prepare)
    assert f'base={base}' in (tmp_path / 'output').read_text().splitlines()
    notes = run('bash', 'scripts/release.sh', base)
    assert 'Initial manual notes' not in notes and 'chore: baseline' not in notes
    assert '## Features' in notes and 'retry transport failures' in notes
    assert '## Breaking Changes' in notes and 'Then update all agents.' in notes
    assert f'/compare/{base}...' in notes


def test_invalid_preparation_never_changes_refs_or_files(release_repo):
    repo, env, run, _, _ = release_repo
    notes = repo / 'docs/initial-release-notes.md'
    notes.write_text('User changes\n')
    refs = run('git', 'show-ref')
    with pytest.raises(subprocess.CalledProcessError, match='non-zero'):
        run('bash', 'scripts/release.sh')
    assert notes.read_text() == 'User changes\n'
    assert run('git', 'show-ref') == refs
    run('git', 'commit', '-am', 'not a conventional commit')
    refs = run('git', 'show-ref')
    with pytest.raises(subprocess.CalledProcessError) as error:
        run('bash', 'scripts/release.sh')
    assert 'Non-conventional commit' in error.value.output
    assert run('git', 'show-ref') == refs
    run('git', 'tag', env['TAG'])  # Collision: this version belongs to the earlier commit.
    run('git', 'checkout', '--detach', 'HEAD^')
    with pytest.raises(subprocess.CalledProcessError):
        run('bash', 'scripts/release.sh')


def test_image_tags_are_reused_and_registry_errors_do_not_rebuild(release_repo):
    _, env, run, calls, _ = release_repo
    env.update(RELEASE_TAG=env['TAG'], CONTAINER_PLATFORMS='linux/amd64,linux/arm64')
    build = ('bash', 'scripts/ci/publish-docker-image.sh', 'drastic-backup-agent', 'apps/agent/Dockerfile', '.')
    run(*build)
    command = json.loads(calls.read_text().splitlines()[-1])
    assert command[:2] == ['buildx', 'build']
    assert f"DRASTIC_VERSION={env['TAG']}" in command
    assert f"DRASTIC_REVISION={run('git', 'rev-parse', 'HEAD')}" in command
    assert not any(':latest' in arg or ':develop' in arg for arg in command)
    env['IMAGE_EXISTS'] = '1'
    calls.write_text('')
    run(*build)
    assert all(json.loads(line)[0] not in ('build', 'push') and json.loads(line)[:2] != ['buildx', 'build']
               for line in calls.read_text().splitlines())
    env['FAIL_DOCKER_ARG'] = f"registry.test/owner/repo/drastic-backup-agent:{env['TAG']}"
    calls.write_text('')
    with pytest.raises(subprocess.CalledProcessError):
        run(*build)
    assert len(calls.read_text().splitlines()) == 2  # Login and inspection; no rebuild on unknown state.
    del env['FAIL_DOCKER_ARG']
    env['RELEASE_TAG'] = 'wrong-tag'
    calls.write_text('')
    with pytest.raises(subprocess.CalledProcessError):
        run(*build)
    assert calls.read_text() == ''
    del env['RELEASE_TAG']
    run(*build)
    assert json.loads(calls.read_text().splitlines()[-1])[-2:] == [
        'registry.test/owner/repo/drastic-backup-agent:develop',
        f"registry.test/owner/repo/drastic-backup-agent:{env['TAG']}",
    ]


def test_approval_checks_both_images_preserves_develop_and_rejects_old_release(release_repo):
    repo, env, run, calls, _ = release_repo
    run('bash', 'scripts/release.sh')
    run('git', 'push', 'origin', f"refs/tags/{env['TAG']}")
    source = run('git', 'rev-parse', 'HEAD')
    (repo / 'new-work.txt').write_text('Development after the draft\n')
    run('git', 'add', '.')
    run('git', 'commit', '-m', 'docs: new work')
    run('git', 'push', 'origin', 'develop')
    develop = run('git', 'rev-parse', 'HEAD')
    run('git', 'checkout', '--detach', source)
    promote = ('bash', 'scripts/ci/promote-release-images.sh', env['TAG'], '--advance-main')
    env.update(IMAGE_EXISTS='1', FAIL_DOCKER_ARG=f"registry.test/owner/repo/drastic-backup-agent:{env['TAG']}")
    refs = run('git', 'ls-remote', 'origin')
    with pytest.raises(subprocess.CalledProcessError):
        run(*promote)
    assert run('git', 'ls-remote', 'origin') == refs
    assert not any(json.loads(line)[:3] == ['buildx', 'imagetools', 'create'] for line in calls.read_text().splitlines())

    # The second alias can fail after main and the server alias have already advanced.
    calls.write_text('')
    env['FAIL_DOCKER_ARG'] = 'registry.test/owner/repo/drastic-backup-agent:latest'
    with pytest.raises(subprocess.CalledProcessError):
        run(*promote)
    assert run('git', 'ls-remote', 'origin', 'refs/heads/main').split()[0] == source
    assert run('git', 'ls-remote', 'origin', 'refs/heads/develop').split()[0] == develop
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    assert [command[-2] for command in commands if command[:3] == ['buildx', 'imagetools', 'create']] == [
        'registry.test/owner/repo/drastic-backup-server:latest', 'registry.test/owner/repo/drastic-backup-agent:latest',
    ]
    del env['FAIL_DOCKER_ARG']
    calls.write_text('')
    for _ in range(2):
        run(*promote)
        assert run('git', 'ls-remote', 'origin', 'refs/heads/main').split()[0] == source
        assert run('git', 'ls-remote', 'origin', 'refs/heads/develop').split()[0] == develop
    # Forgejo promotes the mirrored, approved main without writing Git refs.
    refs = run('git', 'ls-remote', 'origin')
    run(*promote[:-1])
    assert run('git', 'ls-remote', 'origin') == refs
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    assert {command[-2]: command[-1] for command in commands if command[:3] == ['buildx', 'imagetools', 'create']} == {
        f'registry.test/owner/repo/{image}:latest': f"registry.test/owner/repo/{image}:{env['TAG']}"
        for image in ('drastic-backup-server', 'drastic-backup-agent')
    }
    assert all(command[0] not in ('build', 'push') and command[:2] != ['buildx', 'build'] for command in commands)
    run('git', 'push', 'origin', 'develop:main')
    refs = run('git', 'ls-remote', 'origin')
    calls.write_text('')
    with pytest.raises(subprocess.CalledProcessError):
        run(*promote)
    assert calls.read_text() == '' and run('git', 'ls-remote', 'origin') == refs


@pytest.mark.parametrize('conclusion', ['success', 'failure'])
def test_publication_requires_successful_preparation(release_repo, conclusion):
    _, env, run, _, _ = release_repo
    env['PREPARATION_RUNS'] = json.dumps([{
        'head_sha': run('git', 'rev-parse', 'HEAD'), 'head_branch': 'develop',
        'event': 'workflow_dispatch', 'conclusion': conclusion,
    }])
    check = next(step['run'] for step in PUBLISH['publish']['steps']
                 if step.get('name') == 'Require successful preparation of this commit')
    if conclusion == 'success':
        run('bash', '-e', '-o', 'pipefail', '-c', check)
    else:
        with pytest.raises(subprocess.CalledProcessError):
            run('bash', '-e', '-o', 'pipefail', '-c', check)


def test_workflows_keep_preparation_separate_from_approval():
    for name in ('release.yml', 'publish-release.yml'):
        workflow = yaml.safe_load((PROJECT / '.github/workflows' / name).read_text())
        assert workflow['concurrency'] == {'group': 'release', 'queue': 'max', 'cancel-in-progress': False}
    assert PREPARE['tag']['needs'] == 'test'
    assert PREPARE['images']['needs'] == 'tag'
    assert PREPARE['draft']['needs'] == ['tag', 'images']
    assert 'if' not in PREPARE['draft']
    assert PREPARE['test']['if'] == "github.ref == 'refs/heads/develop'"
    publication = yaml.load((PROJECT / '.github/workflows/publish-release.yml').read_text(), Loader=yaml.BaseLoader)
    assert publication['on'] == {'release': {'types': ['published']}}
    assert not any('build' in step.get('uses', '') and 'setup-' not in step['uses'] for step in PUBLISH['publish']['steps'])
    forgejo = yaml.safe_load((PROJECT / '.forgejo/workflows/images.yml').read_text())['jobs']
    assert forgejo['promote']['needs'] == 'publish'
    assert forgejo['promote']['if'] == "github.ref == 'refs/heads/main'"
    assert forgejo['promote']['steps'][-1]['env']['CONTAINER_REGISTRY'] == '${{ vars.CONTAINER_REGISTRY }}'
