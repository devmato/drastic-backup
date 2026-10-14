#!/usr/bin/env python3
"""Git-based Linux/macOS installer using only the standard library."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import getpass
import json
import logging
import os
import platform
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from shlex import quote, split
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from uuid import uuid4

ROOT = Path('/opt/drastic-agent')
WRAPPER = Path('/usr/local/bin/drastic-agent')
MACOS = sys.platform == 'darwin'
SERVICE_LABEL = 'net.drastic.agent'
UPDATE_LABEL = 'net.drastic.agent.update'
SERVICE = Path('/Library/LaunchDaemons/net.drastic.agent.plist') if MACOS else Path('/etc/systemd/system/drastic-agent.service')
MARKER = '.managed-by-drastic-agent'
REPOSITORY = 'https://github.com/devmato/drastic-backup.git'
INDEPENDENT_LIFECYCLE = True
MACOS_LIFECYCLE = True
CONTAINER = False
PROCESS = None
SHUTTING_DOWN = False


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=kwargs.pop('check', True), **kwargs)


def fail(message):
    raise RuntimeError(message)


def read_state():
    path = ROOT / 'install.json'
    if not path.exists():
        return {}
    state = json.loads(path.read_text())
    if not isinstance(state, dict):
        fail('Invalid installation state.')
    return state


def write(path, content, mode=0o600):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content.encode())
            handle.flush()
            os.fsync(handle.fileno())
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def service_text():
    if MACOS:
        return launchd_text(SERVICE_LABEL, 'run', keep_alive=True)
    return f'''[Unit]
Description=dRastic Backup Agent
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile={ROOT}/drastic-agent.env
ExecStart={ROOT}/current/venv/bin/drastic-agent
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=multi-user.target
'''


def launchd_text(label, command, *, keep_alive=False):
    return plistlib.dumps({
        'Label': label,
        'ProgramArguments': [sys.executable, str(ROOT / 'bin/installer.py'), 'log', command],
        'RunAtLoad': True, 'KeepAlive': keep_alive, 'ThrottleInterval': 5,
        'Umask': 0o077,
        # The controller captures even child-process/startup errors in bounded logs.
        'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null',
    }).decode()


def launchd_state(label):
    result = run('launchctl', 'print', f'system/{label}', capture_output=True, text=True, check=False)
    if result.returncode:
        if 'Could not find service' in result.stderr:
            return None
        fail(f'Could not inspect launchd job {label}: {result.stderr.strip()}')
    return result.stdout


def enabled():
    if MACOS:
        result = run('launchctl', 'print-disabled', 'system', capture_output=True, text=True)
        return SERVICE.exists() and not re.search(rf'"{re.escape(SERVICE_LABEL)}"\s*=>\s*true', result.stdout)
    return run('systemctl', 'is-enabled', '--quiet', SERVICE.name, check=False).returncode == 0


def enable_service(enable=True, *, check=True):
    if MACOS:
        run('launchctl', 'enable' if enable else 'disable', f'system/{SERVICE_LABEL}', check=check)
    else:
        systemctl('enable' if enable else 'disable', check=check)


def reload_services():
    if not MACOS:
        run('systemctl', 'daemon-reload')


def check_owned():
    if ROOT == Path('/') or ROOT == Path.home() or ROOT in Path.home().parents:
        fail('Unsafe installation root.')
    if ROOT.is_symlink() or any(parent.is_symlink() for parent in ROOT.parents):
        fail('Installation paths must not contain symlinks.')
    if not (ROOT / MARKER).is_file() or (ROOT / MARKER).is_symlink():
        fail('Installation ownership marker is missing. Run the pipe installer first.')
    for name in ('data', 'data/config.ini', 'tools', 'tools/bin', 'tools/python', 'tools/restic',
                 'cache', 'releases', 'bin', 'install.json', 'drastic-agent.env',
                 'launcher.pid', 'update-request.json', 'bin/installer.py', 'bin/drastic-agent',
                 'bin/update.plist', '.installer.lock', 'stopping.pid'):
        if (ROOT / name).is_symlink():
            fail(f'Unexpected symlink: {ROOT / name}')
    if WRAPPER.exists() or WRAPPER.is_symlink():
        if not WRAPPER.is_symlink() or WRAPPER.readlink() != ROOT / 'bin/drastic-agent':
            fail('The command is not managed by this installer.')
    if SERVICE.exists() or SERVICE.is_symlink():
        owned = False
        if not SERVICE.is_symlink() and MACOS:
            try:
                definition = plistlib.loads(SERVICE.read_bytes())
                if not isinstance(definition, dict):
                    fail('Invalid launchd service definition.')
                arguments = definition.get('ProgramArguments', [])
                owned = (definition.get('Label') == SERVICE_LABEL and isinstance(arguments, list)
                         and len(arguments) >= 3
                         and Path(arguments[0]).is_relative_to(ROOT / 'tools/python')
                         and '..' not in Path(arguments[0]).parts
                         and arguments[1:] in ([str(ROOT / 'bin/installer.py'), 'run'],
                                              [str(ROOT / 'bin/installer.py'), 'log', 'run'])
                         and SERVICE.stat().st_uid == os.geteuid()
                         and not SERVICE.stat().st_mode & 0o022)
            except (plistlib.InvalidFileException, ValueError, TypeError):
                pass
        elif not SERVICE.is_symlink():
            owned = SERVICE.read_text() == service_text()
        if not owned:
            fail('The service is not managed by this installer.')
    current = ROOT / 'current'
    if current.exists() or current.is_symlink():
        if not current.is_symlink() or current.resolve().parent != ROOT / 'releases':
            fail('Invalid active installation link.')


def systemctl(action, *, check=True):
    return run('systemctl', action, SERVICE.name, check=check)


def active():
    if CONTAINER:
        return PROCESS is not None and PROCESS.poll() is None
    if MACOS:
        state = launchd_state(SERVICE_LABEL)
        return bool(state and re.search(r'^\s*pid = \d+', state, re.M))
    return run('systemctl', 'is-active', '--quiet', SERVICE.name, check=False).returncode == 0


def stop():
    if CONTAINER:
        if active():
            PROCESS.terminate()
            try:
                PROCESS.wait(timeout=30)
            except subprocess.TimeoutExpired:
                fail('The agent process is still running; no installation files were removed.')
        return
    if MACOS:
        state = launchd_state(SERVICE_LABEL)
        pending = ROOT / 'stopping.pid'
        if state is not None:
            pid = re.search(r'^\s*pid = (\d+)', state, re.M)
            if pid:
                write(pending, pid[1])
            run('launchctl', 'bootout', f'system/{SERVICE_LABEL}')
        # Unloading a job alone does not prove its old process has stopped.
        # Retain its PID across installer retries when bootout outlives this stop attempt.
        if pending.exists():
            pid = int(pending.read_text())
            if pid <= 1:
                fail('Invalid stopping process identifier.')
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(1)
            else:
                fail('The agent process is still running; no installation files were removed.')
        if launchd_state(SERVICE_LABEL) is not None:
            fail('The agent service is still loaded; no installation files were removed.')
        pending.unlink(missing_ok=True)
        return
    state = run('systemctl', 'show', '--property=LoadState', '--value', SERVICE.name,
                stdout=subprocess.PIPE, text=True).stdout.strip()
    if state != 'not-found':
        systemctl('stop', check=False)
    if active():
        fail('The agent service is still running; no installation files were removed.')


def start():
    global PROCESS
    if CONTAINER:
        if not SHUTTING_DOWN:
            PROCESS = subprocess.Popen([str(ROOT / 'current/venv/bin/drastic-agent')],
                                       env={**os.environ, **installation_settings(read_state())})
    else:
        if MACOS:
            run('launchctl', 'bootstrap', 'system', SERVICE)
        else:
            systemctl('start')


def data_directory():
    return Path(os.environ.get('DRASTIC_AGENT_DATA_DIR', '/app/data')) if CONTAINER else ROOT / 'data'


def installation_settings(state):
    settings = {'DRASTIC_ENV': os.environ.get('DRASTIC_ENV', 'prod') if CONTAINER else 'prod',
                'DRASTIC_AGENT_DATA_DIR': str(data_directory()), 'DRASTIC_AGENT_INSTALL_SOURCE': 'git',
                'DRASTIC_AGENT_INSTALL_VERSION': state.get('version', 'unknown'), 'DRASTIC_AGENT_INSTALL_REF': state['commit'],
                'XDG_CACHE_HOME': str(ROOT / 'cache'), 'RESTIC_CACHE_DIR': str(ROOT / 'cache/restic')}
    if state.get('server'):
        settings['DRASTIC_SERVER'] = state['server']
    if CONTAINER:
        settings['DRASTIC_AGENT_DEPLOYMENT'] = 'docker'
    return settings


def write_wrapper(settings, manager_source=None):
    """Persist the controller separately so agent downgrades cannot restore ref-pinned updates."""
    (ROOT / 'bin').mkdir(exist_ok=True)
    manager = Path(__file__).read_text()
    if manager_source is not None:
        candidate = manager_source.read_text()
        # Only releases that preserve this independent-controller contract may replace it.
        if '\nINDEPENDENT_LIFECYCLE = True\n' in candidate and (
            not MACOS or '\nMACOS_LIFECYCLE = True\n' in candidate
        ):
            manager = candidate
    write(ROOT / 'bin/installer.py', manager, 0o700)
    exports = '' if CONTAINER else '\n'.join(f'export {key}={quote(value)}' for key, value in settings.items())
    write(ROOT / 'bin/drastic-agent', f'''#!/usr/bin/env bash
set -euo pipefail
case "${{1:-}}" in
    install|update|status|uninstall|run|start-update)
        exec "{sys.executable}" "{ROOT}/bin/installer.py" "$@" ;;
esac
{exports}
case "${{1:-}}" in
    -h|--help)
        "{ROOT}/current/venv/bin/drastic-agent" "$@"
        printf '%s\\n' \\
            '' \\
            'Installation commands:' \\
            '  install [options]' \\
            '  update [options]' \\
            '  status' \\
            '  uninstall [--purge] [--yes]' \\
            '' \\
            'Uninstall is only available for native Linux/macOS installations.' \\
            "Run 'drastic-agent COMMAND --help' for command-specific options."
        exit 0 ;;
esac
exec "{ROOT}/current/venv/bin/drastic-agent" "$@"
''', 0o755)
    WRAPPER.parent.mkdir(parents=True, exist_ok=True)
    if not WRAPPER.is_symlink():
        WRAPPER.symlink_to(ROOT / 'bin/drastic-agent')


def switch(release):
    link = ROOT / '.current-new'
    link.unlink(missing_ok=True)
    link.symlink_to(release)
    link.replace(ROOT / 'current')


def prompt(label, default=''):
    if not sys.stdin.isatty():
        if default:
            return default
        fail(f'{label} must be provided for unattended installation.')
    return input(f'{label}' + (f' [{default}]' if default else '') + ': ').strip() or default


def validate_server(value):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {'http', 'https'} and parsed.hostname
                 and not parsed.username and not parsed.password and not parsed.query
                 and not parsed.fragment and not any(c.isspace() for c in value))
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            valid = False
    except ValueError:
        valid = False
    if not valid:
        fail('Server must be an http(s) URL without credentials, query or fragment.')
    return value.rstrip('/')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_revision(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
        fail('The installation target must be a full Git commit SHA.')
    return value


def target_revision(server):
    server = validate_server(server)
    try:
        with build_opener(NoRedirect).open(server + '/api/agents/installation-target', timeout=30) as response:
            target = json.loads(response.read(4097))
    except (OSError, ValueError) as exc:
        raise RuntimeError('The backend installation target is unavailable; use a published build or --source for local development.') from exc
    if not isinstance(target, dict):
        fail('The backend returned an invalid installation target.')
    revision = validate_revision(target.get('revision'))
    if not isinstance(target.get('version'), str) or not re.fullmatch(
        rf'[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}-{revision[:8]}', target['version'],
    ):
        fail('The backend installation target is not a clean source version.')
    return revision


def fetch_source(repository, revision, source):
    validate_revision(revision)
    run('git', 'init', '--quiet', source)
    run('git', '-C', source, 'remote', 'add', '--', 'origin', repository)
    run('git', '-C', source, 'fetch', '--depth=1', '--no-tags', 'origin', revision,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    commit = run('git', '-C', source, 'rev-parse', 'FETCH_HEAD^{commit}', capture_output=True, text=True).stdout.strip()
    if commit != revision:
        fail('The fetched source does not match the backend commit.')
    run('git', '-C', source, 'checkout', '--detach', revision)


def prepare_restic(python, environment):
    """Verify the target's restic without initializing the agent runtime or its database."""
    folder = ROOT / 'tools/restic' if MACOS else data_directory() / 'bin'
    run(python, '-c',
        'import platform, sys; from drastic_agent.integrations.restic_binary import ensure_binary; '
        'machine = platform.machine().lower(); '
        'arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine); '
        'ensure_binary(sys.argv[1], sys.argv[2], arch)',
        folder, 'darwin' if MACOS else 'linux', env=environment)
    if MACOS:
        macos_program_permissions(*folder.glob('restic_*_darwin_*'))


def prepare_release(args, state, server):
    releases = ROOT / 'releases'
    releases.mkdir(exist_ok=True)
    release = Path(tempfile.mkdtemp(dir=releases))
    try:
        source = release / 'source'
        checkout = Path(args.source).resolve() if args.source else release / 'checkout'
        repository = args.repository or state.get('repository') or REPOSITORY
        # Legacy refs remain readable metadata, but never choose a default update target.
        revision = getattr(args, 'revision', None)
        if revision:
            validate_revision(revision)
        elif not args.ref and not args.source:
            revision = target_revision(server)
        ref = args.ref or revision
        if args.ref and (args.ref.startswith('-') or revision):
            fail('Invalid Git ref.')
        if args.source:
            if checkout == ROOT or checkout in ROOT.parents or not (checkout / 'apps/agent/pyproject.toml').is_file():
                fail('The source must be a project checkout outside the installation root or one of its source snapshots.')
            commit = run('git', '-c', f'safe.directory={checkout}',
                         '-C', checkout, 'rev-parse', 'HEAD', capture_output=True, text=True).stdout.strip()
        elif revision:
            fetch_source(repository, revision, checkout)
            commit = revision
        else:
            run('git', 'clone', '--no-checkout', '--', repository, checkout)
            remote_ref = f'refs/remotes/origin/{ref}'
            branch = run('git', '-C', checkout, 'show-ref', '--verify', '--quiet', remote_ref, check=False).returncode == 0
            run('git', '-C', checkout, 'checkout', '--detach', remote_ref if branch else ref)
            commit = run('git', '-C', checkout, 'rev-parse', 'HEAD', capture_output=True, text=True).stdout.strip()
        if revision and commit != revision:
            fail('The source does not match the backend commit.')
        if not (checkout / 'scripts/drastic-agent-installer.py').is_file():
            fail('This source does not contain the native lifecycle manager.')
        if MACOS and '\nMACOS_LIFECYCLE = True\n' not in (checkout / 'scripts/drastic-agent-installer.py').read_text():
            fail('This release does not support macOS. Select a macOS-capable backend build or an explicit --ref.')
        if not (checkout / 'apps/agent/src/drastic_agent/integrations/restic_binary.py').is_file():
            fail('This release lacks the supported restic downloader. Select a newer agent release.')
        included = {
            '.': {'apps', 'libs', 'scripts'},
            'apps': {'agent'},
            'libs': {'python'},
            'libs/python': {'common'},
            'apps/agent': {'src', 'pyproject.toml', 'uv.lock', 'README.md'},
            'libs/python/common': {'src', 'pyproject.toml', 'setup.py'},
            'scripts': {'drastic-agent-installer.py', 'install-agent-dependencies.sh'},
        }
        generated = shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info', '.env*',
                                           'build-version.txt', 'build-revision.txt')

        def ignore_checkout_files(directory, names):
            allowed = included.get(Path(directory).relative_to(checkout).as_posix())
            return set(names) - allowed if allowed is not None else generated(directory, names)

        shutil.copytree(checkout, source, symlinks=True, ignore=ignore_checkout_files)
        version_script = source / 'libs/python/common/src/drastic_common/version.py'
        version = 'unknown'
        if version_script.is_file():
            # Read the untouched checkout so excluded files do not make a clean build dirty.
            run(sys.executable, version_script, '--source', checkout,
                '--output', version_script.with_name('build-version.txt'))
            # Older target releases understand --output but not --revision-output.
            write(version_script.with_name('build-revision.txt'), commit + '\n', 0o644)
            version = version_script.with_name('build-version.txt').read_text().strip()
        if revision and not re.fullmatch(rf'[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}-{revision[:8]}', version):
            fail('The source is not a clean version of the backend commit.')
        if not args.source:
            shutil.rmtree(checkout)
        environment = {**os.environ, 'UV_CACHE_DIR': str(ROOT / 'cache/uv'),
                       'UV_PYTHON_INSTALL_DIR': str(ROOT / 'tools/python'),
                       'UV_PROJECT_ENVIRONMENT': str(release / 'venv')}
        run(ROOT / 'tools/bin/uv', 'sync', '--project', source / 'apps/agent', '--frozen',
            '--no-dev', '--no-editable', '--refresh-package', 'drastic-agent',
            '--refresh-package', 'drastic-common', '--python', sys.executable, env=environment)
        # CLI imports initialize SQLite; never migrate live state while staging a release.
        with tempfile.TemporaryDirectory(dir=release, prefix='.preflight-') as probe_data:
            run(release / 'venv/bin/drastic-agent', '--help', stdout=subprocess.DEVNULL,
                env={**environment, 'DRASTIC_AGENT_DATA_DIR': probe_data})
        if CONTAINER:
            run(sys.executable, source / 'scripts/drastic-agent-installer.py', 'run', '--help',
                stdout=subprocess.DEVNULL)
        dependencies = source / 'scripts/install-agent-dependencies.sh'
        if dependencies.is_file():
            try:
                run('bash', dependencies, env={**os.environ, **({'DRASTIC_AGENT_DEPLOYMENT': 'docker'} if CONTAINER else {})})
            except (OSError, subprocess.CalledProcessError) as exc:
                if CONTAINER:
                    raise
                print(f'Optional dependencies were not installed: {exc}. Continuing agent installation.', file=sys.stderr)
        # Versioned paths keep the running release's restic intact on download failure.
        # Do this before stop(): --help alone never installs or validates restic.
        prepare_restic(release / 'venv/bin/python', environment)
        new_state = {'repository': repository, 'ref': ref or commit, 'commit': commit, 'version': version}
        if CONTAINER:
            new_state['deployment'] = 'docker'
        return release, new_state
    except BaseException:
        shutil.rmtree(release)
        raise


def install(args):
    state = read_state()
    if args.command == 'update' and not state:
        fail('No managed installation exists. Run the pipe installer first.')
    data = data_directory()
    if data.is_symlink() or (data / 'config.ini').is_symlink():
        fail('Agent data paths must not be symlinks.')
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    data.chmod(0o700)
    if MACOS:
        macos_program_permissions(Path(sys.executable))
    status = {'uuid': str(uuid4()), 'state': 'running', 'logs': []}
    status_path = data / f'update-{status["uuid"]}.json'

    def progress(message, result='running'):
        if args.command != 'update':
            return
        now = datetime.now(timezone.utc).isoformat()
        status.update(state=result, ended=now if result != 'running' else None)
        status.setdefault('started', now)
        status['logs'].append({'sequence': len(status['logs']) + 1, 'created': now,
                               'level': 'error' if result == 'failed' else 'info', 'message': message})
        # ponytail: only lifecycle milestones cross the restart; verbose output stays in the journal/container log.
        write(status_path, json.dumps(status) + '\n')

    progress('Agent update started; preparing release')
    try:
        _install(args, state, data, progress)
    except BaseException as exc:
        detail = (f'{Path(exc.cmd[0]).name} exited with code {exc.returncode}'
                  if isinstance(exc, subprocess.CalledProcessError) else str(exc) or type(exc).__name__)
        progress(f'Agent update failed: {detail}', 'failed')
        raise
    progress('Agent update completed; startup checks passed', 'success')


def _install(args, state, data, progress):
    config = configparser.ConfigParser(interpolation=None)
    config.read(data / 'config.ini')
    configured = config.has_section('AGENT')
    saved_server = state.get('server') or config.get('SERVER', 'url', fallback='')
    requested_server = args.server or (os.environ.get('DRASTIC_SERVER') if CONTAINER else None)
    server = validate_server(requested_server or saved_server or prompt('Server URL'))
    if saved_server and validate_server(saved_server) != server:
        fail('The server cannot change while the agent identity is preserved.')
    user = password = None
    if not configured:
        user = args.user or prompt('Username')
        password = args.password
        if not password:
            if not sys.stdin.isatty():
                fail('--password is required for unattended registration.')
            password = getpass.getpass('Password: ')
        if not user or not password:
            fail('Registration credentials must not be empty.')

    release, new_state = prepare_release(args, state, server)
    progress(f'Release prepared: {new_state["ref"]} ({new_state["commit"]})')
    new_state['server'] = server
    was_active = active()
    was_enabled = not CONTAINER and enabled()
    old_release = (ROOT / 'current').resolve() if (ROOT / 'current').exists() else None
    files = [ROOT / 'drastic-agent.env', ROOT / 'install.json', ROOT / 'bin/drastic-agent', ROOT / 'bin/installer.py']
    if not CONTAINER:
        files.append(SERVICE)
    snapshots = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None for path in files}
    settings = installation_settings(new_state)
    try:
        progress('Stopping agent and activating release')
        stop()
        if not configured:
            run(release / 'venv/bin/drastic-agent', 'register', env={**os.environ, **settings,
                'DRASTIC_USER': user, 'DRASTIC_PASSWORD': password})
            config.read(data / 'config.ini')
            if not config.has_section('AGENT'):
                fail('Agent registration did not complete.')
        # Preserve additional configuration (e.g. Proxmox); replace only managed settings.
        env_path = ROOT / 'drastic-agent.env'
        env = env_path.read_text().splitlines() if env_path.exists() else []
        env = [line for line in env if line.split('=', 1)[0] not in {*settings, 'DRASTIC_USER', 'DRASTIC_PASSWORD'}]
        env.extend(f'{key}={json.dumps(value, ensure_ascii=False)}' for key, value in settings.items())
        write(env_path, '\n'.join(env) + '\n')
        write(ROOT / 'install.json', json.dumps(new_state, indent=2) + '\n')
        write_wrapper(settings, release / 'source/scripts/drastic-agent-installer.py')
        switch(release)
        if not CONTAINER:
            # Render with the activated controller, not the updater's old in-memory template.
            definition = run(sys.executable, '-c',
                             'import runpy, sys; from pathlib import Path; '
                             'controller = runpy.run_path(sys.argv[1]); '
                             'render = controller["service_text"]; '
                             'render.__globals__.update(ROOT=Path(sys.argv[2]), MACOS=sys.argv[3] == "darwin"); '
                             'print(render(), end="")',
                             ROOT / 'bin/installer.py', ROOT, 'darwin' if MACOS else 'linux',
                             capture_output=True, text=True).stdout
            write(SERVICE, definition, 0o644)
            reload_services()
            enable_service()
        progress('Starting agent and checking startup')
        start()
        for _ in range(3):
            time.sleep(1)
            if not active():
                fail('The new agent failed to start.' if CONTAINER else 'The new agent service failed to start.')
    except BaseException:
        progress('Installation failed; rolling back')
        try:
            stop()
        except (OSError, RuntimeError) as exc:
            recovery = 'stop the container' if CONTAINER else f'stop {SERVICE.name}'
            raise RuntimeError(
                'Rollback blocked: the agent could not be confirmed stopped. '
                f'Both releases were kept; {recovery} and retry the installation.'
            ) from exc
        if old_release:
            switch(old_release)
        else:
            (ROOT / 'current').unlink(missing_ok=True)
            WRAPPER.unlink(missing_ok=True)
        if not CONTAINER and not was_enabled:
            enable_service(False, check=False)
        for path, snapshot in snapshots.items():
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                write(path, snapshot[0].decode(), snapshot[1])
        if not CONTAINER:
            reload_services()
        if was_active:
            start()
        shutil.rmtree(release)
        progress('Previous installation restored')
        raise
    if old_release:
        shutil.rmtree(old_release)
    print(f'Agent installed at {ROOT}. Status: sudo drastic-agent status')
    if MACOS:
        macos_permissions()


def macos_program_permissions(*programs):
    """Expose only tool executables and their parents to the macOS permission picker."""
    paths = set()
    for program in programs:
        path = Path(program)
        if not path.is_relative_to(ROOT / 'tools') or '..' in path.parts:
            fail('The macOS program must be inside the private tools directory.')
        for candidate in (path, path.resolve(strict=True)):
            paths.add(candidate)
            for parent in candidate.parents:
                paths.add(parent)
                if parent == ROOT:
                    break
    # Check every ancestor before chmod: an intermediate link can lead outside tools.
    for path in paths:
        if path != ROOT and not path.resolve(strict=True).is_relative_to(ROOT / 'tools'):
            fail('The macOS program and its parents must resolve inside the private tools directory.')
    for path in paths:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | 0o555)


def macos_permissions():
    """Print actual executables: Terminal permissions do not authorize a LaunchDaemon."""
    print('For protected files, open System Settings > Privacy & Security > Full Disk Access.\n'
          'Use + and Cmd+Shift+G to add these executables (not Terminal or the shell wrapper):')
    print(f'  {(ROOT / "current/venv/bin/python").resolve()}')
    constant = ROOT / 'current/source/libs/python/common/src/drastic_common/restic/constants.py'
    version = re.search(r'RESTIC_VERSION\s*=\s*[\'"]([^\'"]+)', constant.read_text()) if constant.is_file() else None
    if version:
        machine = platform.machine().lower()
        arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(machine, machine)
        print(f'  {ROOT / "tools/restic" / f"restic_{version[1]}_darwin_{arch}"}')
    print('Then restart the service: sudo launchctl kickstart -k system/net.drastic.agent\n'
          'Run a file backup from the web UI to verify access from the background service.\n'
          'After Python/restic changes, macOS may require granting access again.')


def run_macos():
    """Use the private interpreter directly and preserve the existing environment file."""
    environment = {**os.environ, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin'}
    for line in (ROOT / 'drastic-agent.env').read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        environment[key.strip()] = ' '.join(split(value, comments=True))
    python = ROOT / 'current/venv/bin/python'
    os.execve(python, [str(python), '-m', 'drastic_agent'], environment)


def run_logged(command):
    """Capture daemon/updater output in bounded chunks, preserving signals and exit status."""
    check_owned()
    label = SERVICE_LABEL if command == 'run' else UPDATE_LABEL
    path = ROOT / 'data' / f'{label}.log'
    for log in (path, Path(f'{path}.1'), Path(f'{path}.2')):
        if log.is_symlink():
            fail('Agent logs must not be symlinks.')
    args = [sys.executable, '-u', str(ROOT / 'bin/installer.py'), command]
    if command == 'update':
        args.append('--wait-lock')
    previous_mask = os.umask(0o077)
    handler = None
    try:
        handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
        path.chmod(0o600)
        handler.terminator = ''
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT) as process:
            previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

            def forward(sig, _frame):
                try:
                    process.send_signal(sig)
                except ProcessLookupError:
                    pass

            for sig in previous:
                signal.signal(sig, forward)
            try:
                # A newline-free tool must not accumulate unbounded output in RAM.
                while chunk := process.stdout.read1(8192):
                    handler.emit(logging.LogRecord('drastic', logging.INFO, '', 0,
                                                   chunk.decode(errors='replace'), (), None))
                code = process.wait()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                for sig, previous_handler in previous.items():
                    signal.signal(sig, previous_handler)
    finally:
        if handler is not None:
            handler.close()
        os.umask(previous_mask)
    return code if code >= 0 else 128 - code


def start_macos_update():
    """launchd owns the updater so stopping the agent cannot kill its installer."""
    state = launchd_state(UPDATE_LABEL)
    if state is not None:
        if not (re.search(r'^\s*state = not running\s*$', state, re.M)
                and re.search(r'^\s*last (exit code = -?\d+|terminating signal = [^\n]*\b[1-9]\d*)\s*$', state, re.M)):
            fail('An update is already running.')
        run('launchctl', 'bootout', f'system/{UPDATE_LABEL}')
    path = ROOT / 'bin/update.plist'
    write(path, launchd_text(UPDATE_LABEL, 'update'), 0o600)
    run('launchctl', 'enable', f'system/{UPDATE_LABEL}')
    run('launchctl', 'bootstrap', 'system', path)


def initialize_image(args):
    """Adopt the release already built by Docker; no downloads or registration."""
    global CONTAINER
    if read_state():
        fail('An installation already exists.')
    CONTAINER = True
    (ROOT / MARKER).touch()
    ROOT.chmod(0o700)
    check_owned()
    stamp = ROOT / 'releases/image/source/libs/python/common/src/drastic_common/build-version.txt'
    revision_stamp = stamp.with_name('build-revision.txt')
    commit = revision_stamp.read_text().strip() if revision_stamp.is_file() else 'unknown'
    state = {'deployment': 'docker', 'repository': args.repository, 'ref': commit, 'commit': commit}
    state['version'] = stamp.read_text().strip() if stamp.is_file() else 'unknown'
    write(ROOT / 'install.json', json.dumps(state, indent=2) + '\n')
    switch(ROOT / 'releases/image')
    write_wrapper(installation_settings(state))


def check_launcher():
    try:
        os.kill(int((ROOT / 'launcher.pid').read_text()), 0)
    except (OSError, ValueError):
        fail('The managed container launcher is not running.')


def request_update(args):
    check_launcher()
    if (ROOT / 'update-request.json').exists():
        fail('An update is already running.')
    if args.server or args.user or args.password:
        fail('Container updates preserve registration; configure credentials through the container environment.')
    write(ROOT / 'update-request.json', json.dumps({key: getattr(args, key, None) for key in ('repository', 'ref', 'source', 'revision')}) + '\n')
    print('Agent update requested; details: docker logs <agent-container>', flush=True)


def run_container():
    """Own the agent and reuse install() for updates, without systemd."""
    global SHUTTING_DOWN
    if not CONTAINER:
        fail('The run command requires a managed container installation.')
    pid = ROOT / 'launcher.pid'
    request = ROOT / 'update-request.json'
    if pid.exists():
        try:
            check_launcher()
        except RuntimeError:
            pass
        else:
            if int(pid.read_text()) != os.getpid():
                fail('The container launcher is already running.')

    def shutdown(_signum, _frame):
        global SHUTTING_DOWN
        SHUTTING_DOWN = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    write(pid, str(os.getpid()) + '\n')
    # ponytail: updates live in the container layer; a restart retries an interrupted request.
    try:
        start()
        while active():
            if request.exists():
                descriptor = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    options = json.loads(request.read_text())
                    install(argparse.Namespace(command='update', server=None, user=None, password=None, **options))
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                    print(f'Agent update failed: {exc}', file=sys.stderr, flush=True)
                finally:
                    request.unlink(missing_ok=True)
                    os.close(descriptor)
            time.sleep(0.5)
        raise SystemExit(PROCESS.returncode or 0)
    except KeyboardInterrupt:
        if not SHUTTING_DOWN:
            raise
    finally:
        stop()
        pid.unlink(missing_ok=True)


def uninstall(args):
    if not args.yes:
        if not sys.stdin.isatty():
            fail('Unattended uninstall requires --yes.')
        if prompt('Remove agent' + (' and all local state' if args.purge else ' (keep state)') + '? [y/N]', 'n').lower() not in {'y', 'yes'}:
            return
    stop()
    if SERVICE.exists():
        enable_service(False)
        SERVICE.unlink()
    WRAPPER.unlink(missing_ok=True)
    reload_services()
    if MACOS:
        if launchd_state(UPDATE_LABEL) is not None:
            run('launchctl', 'bootout', f'system/{UPDATE_LABEL}')
    else:
        systemctl('reset-failed', check=False)
    if args.purge:
        shutil.rmtree(ROOT)
    else:
        for child in ROOT.iterdir():
            if child.name in {'data', 'drastic-agent.env', 'install.json', MARKER, '.installer.lock'}:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    print('Agent removed.' + ('' if args.purge else f' State retained in {ROOT}.'))


def main():
    global CONTAINER
    parser = argparse.ArgumentParser(description='dRastic Agent lifecycle manager')
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('install', 'update'):
        sub = commands.add_parser(command)
        sub.add_argument('--repository')
        sub.add_argument('--ref', help='Explicit source override for this invocation; subsequent updates follow the backend')
        sub.add_argument('--revision', help='Exact backend commit selected by the bootstrap installer')
        sub.add_argument('--source', help='Install a local checkout instead of fetching Git')
        sub.add_argument('--server')
        sub.add_argument('--user')
        sub.add_argument('--password')
        if command == 'update':
            sub.add_argument('--wait-lock', action='store_true', help=argparse.SUPPRESS)
    commands.add_parser('status')
    commands.add_parser('run')
    commands.add_parser('start-update', help=argparse.SUPPRESS)
    log = commands.add_parser('log', help=argparse.SUPPRESS)
    log.add_argument('action', choices=('run', 'update'))
    image = commands.add_parser('image', help='Initialize a prebuilt Docker release')
    image.add_argument('--repository', default=REPOSITORY)
    sub = commands.add_parser('uninstall', description='Remove the native agent; keep local identity and settings unless --purge is used.')
    sub.add_argument('--purge', action='store_true', help='Also remove all local agent data, identity and settings')
    sub.add_argument('--yes', action='store_true', help='Skip confirmation (required for unattended uninstall)')
    args = parser.parse_args()
    try:
        if sys.platform not in {'linux', 'darwin'} or os.geteuid() != 0:
            fail('Run this command as root on Linux or macOS (sudo drastic-agent ...).')
        if args.command == 'log':
            if not MACOS:
                fail('The log command is only used by macOS services.')
            raise SystemExit(run_logged(args.action))
        if args.command == 'image':
            if MACOS:
                fail('Docker image initialization requires Linux.')
            initialize_image(args)
            return
        CONTAINER = read_state().get('deployment') == 'docker'
        check_owned()
        if args.command == 'run':
            if MACOS:
                run_macos()
            else:
                run_container()
            return
        if CONTAINER and args.command not in {'update', 'status'}:
            fail('Manage installation and removal through the container deployment.')
        # Linux locks the directory; macOS uses a file so APFS need not support directory locks.
        descriptor = (os.open(ROOT / '.installer.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                      if MACOS else os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY))
        try:
            # The launchd updater can start before its launching controller releases the lock.
            deadline = time.monotonic() + (10 if getattr(args, 'wait_lock', False) else 0)
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            if CONTAINER and args.command == 'update':
                request_update(args)
            elif args.command in {'install', 'update'}:
                install(args)
            elif args.command == 'start-update':
                if not MACOS:
                    fail('start-update is only used by macOS agents.')
                start_macos_update()
            elif args.command == 'status':
                print(json.dumps(read_state(), indent=2))
                if CONTAINER:
                    check_launcher()
                elif MACOS:
                    print(launchd_state(SERVICE_LABEL) or 'Agent service is not loaded.')
                    macos_permissions()
                else:
                    systemctl('status', check=False)
            else:
                uninstall(args)
        finally:
            os.close(descriptor)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, configparser.Error) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
