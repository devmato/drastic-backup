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
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from shlex import quote
from urllib.parse import urlsplit

ROOT = Path('/opt/drastic-agent')
WRAPPER = Path('/usr/local/bin/drastic-agent')
SERVICE = Path('/etc/systemd/system/drastic-agent.service')
MARKER = '.managed-by-drastic-agent'
REPOSITORY = 'https://github.com/devmato/drastic-backup.git'


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
    for name in ('data', 'data/config.ini', 'tools', 'cache', 'releases', 'bin', 'install.json', 'drastic-agent.env'):
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
    return run('systemctl', 'is-active', '--quiet', SERVICE.name, check=False).returncode == 0


def stop():
    systemctl('stop', check=False)
    if active():
        fail('The agent service is still running; no installation files were removed.')


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
        environment = {**os.environ, 'UV_CACHE_DIR': str(ROOT / 'cache/uv'),
                       'UV_PYTHON_INSTALL_DIR': str(ROOT / 'tools/python'),
                       'UV_PROJECT_ENVIRONMENT': str(release / 'venv')}
        run(ROOT / 'tools/bin/uv', 'sync', '--project', source / 'apps/agent', '--frozen',
            '--no-dev', '--no-editable', '--python', sys.executable, env=environment)
        run(release / 'venv/bin/drastic-agent', '--help', stdout=subprocess.DEVNULL)
        return release, {'repository': repository, 'ref': ref, 'commit': commit}
    except BaseException:
        shutil.rmtree(release)
        raise


def install(args):
    state = read_state()
    if args.command == 'update' and not state:
        fail('No managed installation exists. Run the pipe installer first.')
    data = ROOT / 'data'
    data.mkdir(mode=0o700, exist_ok=True)
    data.chmod(0o700)
    config = configparser.ConfigParser(interpolation=None)
    config.read(data / 'config.ini')
    configured = config.has_section('AGENT')
    saved_server = state.get('server') or config.get('SERVER', 'url', fallback='')
    server = validate_server(args.server or saved_server or prompt('Server URL'))
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
    new_state['server'] = server
    was_active = active()
    was_enabled = run('systemctl', 'is-enabled', '--quiet', SERVICE.name, check=False).returncode == 0
    old_release = (ROOT / 'current').resolve() if (ROOT / 'current').exists() else None
    (ROOT / 'bin').mkdir(exist_ok=True)
    files = (ROOT / 'drastic-agent.env', ROOT / 'install.json', ROOT / 'bin/drastic-agent', SERVICE)
    snapshots = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None for path in files}
    settings = {'DRASTIC_ENV': 'prod', 'DRASTIC_SERVER': server,
                'DRASTIC_AGENT_DATA_DIR': str(data), 'DRASTIC_AGENT_INSTALL_SOURCE': 'git',
                'DRASTIC_AGENT_INSTALL_VERSION': new_state['ref'],
                'DRASTIC_AGENT_INSTALL_REF': new_state['commit'],
                'XDG_CACHE_HOME': str(ROOT / 'cache'),
                'RESTIC_CACHE_DIR': str(ROOT / 'cache/restic')}
    try:
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
        exports = '\n'.join(f'export {key}={quote(value)}' for key, value in settings.items())
        write(ROOT / 'bin/drastic-agent', f'''#!/usr/bin/env bash
set -euo pipefail
case "${{1:-}}" in
    install|update|status|uninstall)
        exec "{sys.executable}" "{ROOT}/current/source/scripts/drastic-agent-installer.py" "$@" ;;
esac
{exports}
exec "{ROOT}/current/venv/bin/drastic-agent" "$@"
''', 0o755)
        switch(release)
        WRAPPER.parent.mkdir(parents=True, exist_ok=True)
        if not WRAPPER.is_symlink():
            WRAPPER.symlink_to(ROOT / 'bin/drastic-agent')
        write(SERVICE, service_text(), 0o644)
        run('systemctl', 'daemon-reload')
        systemctl('enable')
        systemctl('start')
        for _ in range(3):
            time.sleep(1)
            if not active():
                fail('The new agent service failed to start.')
    except BaseException:
        try:
            stop()
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(
                'Rollback blocked: the agent service could not be confirmed stopped. '
                'Both releases were kept; stop drastic-agent.service and retry the installation.'
            ) from exc
        if old_release:
            switch(old_release)
        else:
            (ROOT / 'current').unlink(missing_ok=True)
            WRAPPER.unlink(missing_ok=True)
        if not was_enabled:
            systemctl('disable', check=False)
        for path, snapshot in snapshots.items():
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                write(path, snapshot[0].decode(), snapshot[1])
        run('systemctl', 'daemon-reload')
        if was_active:
            systemctl('start')
        shutil.rmtree(release)
        raise
    if old_release:
        shutil.rmtree(old_release)
    print(f'Agent installed at {ROOT}. Status: sudo drastic-agent status')


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
    sub = commands.add_parser('uninstall')
    sub.add_argument('--purge', action='store_true')
    sub.add_argument('--yes', action='store_true')
    args = parser.parse_args()
    try:
        if sys.platform != 'linux' or os.geteuid() != 0:
            fail('Run this command as root on Linux (sudo drastic-agent ...).')
        check_owned()
        # Lock the directory itself; there is no stale lock file after a purge.
        descriptor = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command in {'install', 'update'}:
                install(args)
            elif args.command == 'status':
                print(json.dumps(read_state(), indent=2))
                systemctl('status', check=False)
            else:
                uninstall(args)
        finally:
            os.close(descriptor)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, configparser.Error) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
