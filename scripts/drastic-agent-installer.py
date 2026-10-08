#!/usr/bin/env python3
"""Git-based Linux installer using only the standard library."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import getpass
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from shlex import quote
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path('/opt/drastic-agent')
WRAPPER = Path('/usr/local/bin/drastic-agent')
SERVICE = Path('/etc/systemd/system/drastic-agent.service')
MARKER = '.managed-by-drastic-agent'
REPOSITORY = 'https://github.com/devmato/drastic-backup.git'
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


def check_owned():
    if ROOT == Path('/') or ROOT == Path.home() or ROOT in Path.home().parents:
        fail('Unsafe installation root.')
    if ROOT.is_symlink() or any(parent.is_symlink() for parent in ROOT.parents):
        fail('Installation paths must not contain symlinks.')
    if not (ROOT / MARKER).is_file() or (ROOT / MARKER).is_symlink():
        fail('Installation ownership marker is missing. Run the pipe installer first.')
    for name in ('data', 'data/config.ini', 'tools', 'cache', 'releases', 'bin', 'install.json', 'drastic-agent.env',
                 'launcher.pid', 'update-request.json'):
        if (ROOT / name).is_symlink():
            fail(f'Unexpected symlink: {ROOT / name}')
    if WRAPPER.exists() or WRAPPER.is_symlink():
        if not WRAPPER.is_symlink() or WRAPPER.readlink() != ROOT / 'bin/drastic-agent':
            fail('The command is not managed by this installer.')
    if SERVICE.exists() or SERVICE.is_symlink():
        if SERVICE.is_symlink() or SERVICE.read_text() != service_text():
            fail('The systemd service is not managed by this installer.')
    current = ROOT / 'current'
    if current.exists() or current.is_symlink():
        if not current.is_symlink() or current.resolve().parent != ROOT / 'releases':
            fail('Invalid active installation link.')


def systemctl(action, *, check=True):
    return run('systemctl', action, SERVICE.name, check=check)


def active():
    if CONTAINER:
        return PROCESS is not None and PROCESS.poll() is None
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


def write_wrapper(settings):
    (ROOT / 'bin').mkdir(exist_ok=True)
    exports = '' if CONTAINER else '\n'.join(f'export {key}={quote(value)}' for key, value in settings.items())
    write(ROOT / 'bin/drastic-agent', f'''#!/usr/bin/env bash
set -euo pipefail
case "${{1:-}}" in
    install|update|status|uninstall|run)
        exec "{sys.executable}" "{ROOT}/current/source/scripts/drastic-agent-installer.py" "$@" ;;
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
            'Uninstall is only available for native Linux installations.' \\
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


def prepare_release(args, state):
    releases = ROOT / 'releases'
    releases.mkdir(exist_ok=True)
    release = Path(tempfile.mkdtemp(dir=releases))
    try:
        source = release / 'source'
        repository = args.repository or state.get('repository') or REPOSITORY
        ref = args.ref or state.get('ref') or 'main'
        if ref.startswith('-') or not ref:
            fail('Invalid Git ref.')
        if args.source:
            checkout = Path(args.source).resolve()
            if checkout == ROOT or checkout in ROOT.parents or not (checkout / 'apps/agent/pyproject.toml').is_file():
                fail('The source must be a project checkout outside the installation root or one of its source snapshots.')
            shutil.copytree(checkout, source, symlinks=True,
                            ignore=shutil.ignore_patterns('.git', '.venv', 'node_modules', 'data',
                                                         'storage', 'dist', 'site', '__pycache__',
                                                         '.pytest_cache', '.ruff_cache', '.env*'))
            commit = run('git', '-c', f'safe.directory={Path(args.source).resolve()}',
                         '-C', args.source, 'rev-parse', 'HEAD', capture_output=True, text=True).stdout.strip()
        else:
            run('git', 'clone', '--no-checkout', '--', repository, source)
            remote_ref = f'refs/remotes/origin/{ref}'
            branch = run('git', '-C', source, 'show-ref', '--verify', '--quiet', remote_ref, check=False).returncode == 0
            run('git', '-C', source, 'checkout', '--detach', remote_ref if branch else ref)
            commit = run('git', '-C', source, 'rev-parse', 'HEAD', capture_output=True, text=True).stdout.strip()
        if not (source / 'scripts/drastic-agent-installer.py').is_file():
            fail('This Git ref does not contain the native lifecycle manager.')
        version_script = source / 'libs/python/common/src/drastic_common/version.py'
        version = 'unknown'
        if version_script.is_file():
            run(sys.executable, version_script, '--source', args.source or source,
                '--output', version_script.with_name('build-version.txt'))
            version = version_script.with_name('build-version.txt').read_text().strip()
        environment = {**os.environ, 'UV_CACHE_DIR': str(ROOT / 'cache/uv'),
                       'UV_PYTHON_INSTALL_DIR': str(ROOT / 'tools/python'),
                       'UV_PROJECT_ENVIRONMENT': str(release / 'venv')}
        run(ROOT / 'tools/bin/uv', 'sync', '--project', source / 'apps/agent', '--frozen',
            '--no-dev', '--no-editable', '--refresh-package', 'drastic-agent',
            '--refresh-package', 'drastic-common', '--python', sys.executable, env=environment)
        run(release / 'venv/bin/drastic-agent', '--help', stdout=subprocess.DEVNULL)
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
        new_state = {'repository': repository, 'ref': ref, 'commit': commit, 'version': version}
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

    release, new_state = prepare_release(args, state)
    progress(f'Release prepared: {new_state["ref"]} ({new_state["commit"]})')
    new_state['server'] = server
    was_active = active()
    was_enabled = not CONTAINER and run('systemctl', 'is-enabled', '--quiet', SERVICE.name, check=False).returncode == 0
    old_release = (ROOT / 'current').resolve() if (ROOT / 'current').exists() else None
    files = [ROOT / 'drastic-agent.env', ROOT / 'install.json', ROOT / 'bin/drastic-agent']
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
        write_wrapper(settings)
        switch(release)
        if not CONTAINER:
            write(SERVICE, service_text(), 0o644)
            run('systemctl', 'daemon-reload')
            systemctl('enable')
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
            recovery = 'stop the container' if CONTAINER else 'stop drastic-agent.service'
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
            systemctl('disable', check=False)
        for path, snapshot in snapshots.items():
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                write(path, snapshot[0].decode(), snapshot[1])
        if not CONTAINER:
            run('systemctl', 'daemon-reload')
        if was_active:
            start()
        shutil.rmtree(release)
        progress('Previous installation restored')
        raise
    if old_release:
        shutil.rmtree(old_release)
    print(f'Agent installed at {ROOT}. Status: sudo drastic-agent status')


def initialize_image(args):
    """Adopt the release already built by Docker; no downloads or registration."""
    global CONTAINER
    if read_state():
        fail('An installation already exists.')
    CONTAINER = True
    (ROOT / MARKER).touch()
    ROOT.chmod(0o700)
    check_owned()
    state = {'deployment': 'docker', 'repository': args.repository, 'ref': args.ref, 'commit': args.commit}
    stamp = ROOT / 'releases/image/source/libs/python/common/src/drastic_common/build-version.txt'
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
    write(ROOT / 'update-request.json', json.dumps({key: getattr(args, key) for key in ('repository', 'ref', 'source')}) + '\n')
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
        systemctl('disable')
        SERVICE.unlink()
    WRAPPER.unlink(missing_ok=True)
    run('systemctl', 'daemon-reload')
    systemctl('reset-failed', check=False)
    if args.purge:
        shutil.rmtree(ROOT)
    else:
        for child in ROOT.iterdir():
            if child.name in {'data', 'drastic-agent.env', 'install.json', MARKER}:
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
        sub.add_argument('--ref')
        sub.add_argument('--source', help='Install a local checkout instead of fetching Git')
        sub.add_argument('--server')
        sub.add_argument('--user')
        sub.add_argument('--password')
    commands.add_parser('status')
    commands.add_parser('run')
    image = commands.add_parser('image', help='Initialize a prebuilt Docker release')
    image.add_argument('--repository', default=REPOSITORY)
    image.add_argument('--ref', default='main')
    image.add_argument('--commit', default='unknown')
    sub = commands.add_parser('uninstall', description='Remove the native agent; keep local identity and settings unless --purge is used.')
    sub.add_argument('--purge', action='store_true', help='Also remove all local agent data, identity and settings')
    sub.add_argument('--yes', action='store_true', help='Skip confirmation (required for unattended uninstall)')
    args = parser.parse_args()
    try:
        if sys.platform != 'linux' or os.geteuid() != 0:
            fail('Run this command as root on Linux (sudo drastic-agent ...).')
        if args.command == 'image':
            initialize_image(args)
            return
        CONTAINER = read_state().get('deployment') == 'docker'
        check_owned()
        if args.command == 'run':
            run_container()
            return
        if CONTAINER and args.command not in {'update', 'status'}:
            fail('Manage installation and removal through the container deployment.')
        # Lock the directory itself; there is no stale lock file after a purge.
        descriptor = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if CONTAINER and args.command == 'update':
                request_update(args)
            elif args.command in {'install', 'update'}:
                install(args)
            elif args.command == 'status':
                print(json.dumps(read_state(), indent=2))
                if CONTAINER:
                    check_launcher()
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
