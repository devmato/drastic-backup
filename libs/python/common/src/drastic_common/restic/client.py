import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from queue import Empty, Queue

import drastic_common.restic.parsers as parsers
from drastic_common import diagnostics
from drastic_common.process import is_mounted, mounted_process
from drastic_common.restic.exceptions import (
    ResticBinaryNotFoundError,
    ResticCancelledError,
    ResticFailedError,
    ResticTimeoutError,
)


class ResticApi:
    _processes = {}
    _process_lock = threading.Lock()
    _producer_exit_grace = 1.0

    def __init__(
        self,
        binary_path,
        repository=None,
        cancellation_event=None,
        timeout=None,
        deadline=None,
        terminate_grace=5.0,
    ):
        self._state = threading.local()
        self.binary_path = binary_path
        self.cancellation_event = cancellation_event
        self.timeout = timeout
        self.deadline = deadline
        self.terminate_grace = terminate_grace

        if repository:
            self.set_repository(repository)

    @property
    def repository(self):
        return getattr(self._state, "repository", None)

    @contextmanager
    def operation_cancellation(self, event):
        """Scope cancellation to this worker, without changing other repository users."""
        previous = getattr(self._state, "cancellation_event", None)
        self._state.cancellation_event = event
        try:
            yield
        finally:
            self._state.cancellation_event = previous

    def _cancelled(self):
        event = getattr(self._state, "cancellation_event", None)
        return bool((event is not None and event.is_set()) or
                    (self.cancellation_event is not None and self.cancellation_event.is_set()))

    def __make_command(self, *args, **kwargs):
        cmd = [self.binary_path]
        cmd.extend(["--json"])

        if self.repository:
            cmd.extend(["-r", self.repository.location])

        for arg in args:
            if not isinstance(arg, (list, tuple)):
                arg = [arg]

            cmd.extend(str(arg_value) for arg_value in arg)

        for key, values in kwargs.items():
            if values:
                key = key.replace("_", "-")

                if not isinstance(values, (list, tuple)):
                    values = [values]

                for value in values:
                    if isinstance(value, bool):
                        cmd.extend([f"--{key}"])
                    else:
                        cmd.extend([f"--{key}", str(value)])

        return cmd

    def __build_env(self):
        env = os.environ.copy()

        if self.repository:
            env["RESTIC_PASSWORD"] = self.repository.password or ""
            for key, value in self.repository.env.items():
                env[key] = str(value)

        diagnostics.remember_secrets(env)
        return env

    @contextmanager
    def __restic_env(self):
        env = self.__build_env()
        repository = self.repository
        private_key = getattr(repository, "ssh_private_key", None) if repository else None
        if not private_key:
            yield env
            return

        temp_home = tempfile.mkdtemp(prefix="drastic-restic-ssh-")
        try:
            ssh_dir = os.path.join(temp_home, ".ssh")
            os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
            key_path = os.path.join(ssh_dir, "identity")
            with open(key_path, "w", encoding="utf-8") as key_file:
                key_file.write(str(private_key))
                if not str(private_key).endswith("\n"):
                    key_file.write("\n")
            os.chmod(key_path, 0o600)

            known_hosts_path = getattr(repository, "ssh_known_hosts_path", None)
            if known_hosts_path:
                os.makedirs(os.path.dirname(known_hosts_path), mode=0o700, exist_ok=True)
            else:
                known_hosts_path = os.path.join(ssh_dir, "known_hosts")

            config_path = os.path.join(ssh_dir, "config")
            with open(config_path, "w", encoding="utf-8") as config_file:
                config_file.write(
                    "Host *\n"
                    f"  IdentityFile {key_path}\n"
                    "  IdentitiesOnly yes\n"
                    f"  UserKnownHostsFile {known_hosts_path}\n"
                    "  StrictHostKeyChecking accept-new\n"
                )
            os.chmod(config_path, 0o600)
            env["HOME"] = temp_home
            yield env
        finally:
            shutil.rmtree(temp_home, ignore_errors=True)

    def __register_process(self, pid, process):
        with self._process_lock:
            self._processes[pid] = process

    def __unregister_process(self, pid):
        with self._process_lock:
            self._processes.pop(pid, None)

    @classmethod
    def diagnostic_processes(cls):
        with cls._process_lock:
            return [process.pid for managed in cls._processes.values()
                    for process in getattr(managed, "_processes", [])]

    def __drain_binary_stream(self, stream, output_chunks, callback=None, pid=None, operation_uuid=None):
        try:
            while True:
                try:
                    chunk = stream.readline(64 * 1024)
                except (AttributeError, TypeError):
                    chunk = stream.read()
                    if chunk:
                        output_chunks.append(chunk)
                    break
                if not chunk:
                    break
                output_chunks.append(chunk)
                text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
                diagnostics.local_event("process.stderr", {"pid": pid, "output": text[:8000]}, operation_uuid=operation_uuid)
                if callback is not None:
                    # Progress parsing must never stop draining the pipe or break the backup.
                    try:
                        callback(text)
                    except Exception:
                        logging.exception("Backup source progress callback failed")
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()

    def __join_stream_chunks(self, chunks):
        if not chunks:
            return ""
        if isinstance(chunks[0], bytes):
            return b"".join(chunks).decode("utf-8", errors="replace")
        return "".join(chunks)

    def __deadline(self):
        deadlines = []
        if self.timeout is not None:
            deadlines.append(time.monotonic() + float(self.timeout))
        if self.deadline is not None:
            if isinstance(self.deadline, datetime):
                now = datetime.now(tz=self.deadline.tzinfo)
                deadlines.append(time.monotonic() + max(0, (self.deadline - now).total_seconds()))
            else:
                deadlines.append(float(self.deadline))
        return min(deadlines) if deadlines else None

    def __monitor_output(self, process, callback, callback_args, callback_pid, callback_throttle, deadline, monitor_callback=None):
        output = []
        output_queue = Queue()

        def read_stdout():
            try:
                while True:
                    line = process.stdout.readline()
                    if line == "":
                        break
                    output_queue.put(line)
            finally:
                output_queue.put(None)

        stdout_thread = threading.Thread(target=read_stdout, daemon=True)
        stdout_thread.start()
        last_callback_invoked = None
        stream_closed = False
        last_monitor = 0
        while not (stream_closed and process.poll() is not None):
            if monitor_callback is not None and time.monotonic() - last_monitor >= 5:
                monitor_callback()
                last_monitor = time.monotonic()
            if self._cancelled():
                raise ResticCancelledError("Restic operation was cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise ResticTimeoutError("Restic operation timed out")
            try:
                line = output_queue.get(timeout=0.05)
            except Empty:
                continue
            if line is None:
                stream_closed = True
                continue
            try:
                message = json.loads(line)
            except (ValueError, TypeError):
                message = None
            message_type = message.get("message_type") if isinstance(message, dict) else None
            if message_type in {"summary", "error"}:
                diagnostics.local_event("restic.output", {"pid": process.pid, "status": message},
                                        operation_uuid=callback_args.get("operation_uuid") or getattr(getattr(callback, "__self__", None), "uuid", None))
            if message_type == "verbose_status" and message.get("action") != "scan_finished":
                continue
            # One-shot scan and summary events must survive status throttling.
            essential = message_type in {"summary", "verbose_status"}
            if callback is not None and (
                essential
                or callback_throttle is None
                or last_callback_invoked is None
                or datetime.now() - last_callback_invoked >= timedelta(milliseconds=callback_throttle)
            ):
                current_callback_args = callback_args
                if callback_pid:
                    current_callback_args = {**callback_args, "pid": process.pid}
                callback(line, **current_callback_args)
                last_callback_invoked = datetime.now()
            output.append(line)
        stdout_thread.join()
        return output

    def __popen(self, *args, **kwargs):
        try:
            return subprocess.Popen(*args, **kwargs)
        except TypeError as exc:
            if "start_new_session" not in str(exc):
                raise
            kwargs.pop("start_new_session", None)
            return subprocess.Popen(*args, **kwargs)

    def __execute_command(
        self,
        cmd,
        stdin_data=None,
        callback=None,
        callback_args=None,
        callback_pid=False,
        callback_throttle=None,
        parser=parsers.default,
        cwd=None,
        monitor_callback=None,
    ):
        started = time.monotonic()
        operation_uuid = (callback_args or {}).get("operation_uuid") or getattr(getattr(callback, "__self__", None), "uuid", None)
        if cwd is not None:
            # Changing the source directory must not relocate the binary or local repository.
            cmd = list(cmd)
            if os.path.dirname(cmd[0]):
                cmd[0] = os.path.abspath(cmd[0])
            if "-r" in cmd:
                index = cmd.index("-r") + 1
                if ":" not in cmd[index]:
                    cmd[index] = os.path.abspath(cmd[index])
        logging.debug(f"Executing command: {cmd}")
        if self._cancelled():
            raise ResticCancelledError("Restic operation was cancelled")
        callback_args = dict(callback_args or {})
        deadline = self.__deadline()

        restic_env_context = None
        process = None
        try:
            restic_env_context = self.__restic_env()
            env = restic_env_context.__enter__()
            process = self.__popen(
                cmd,
                stdin=subprocess.PIPE if stdin_data is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                env=env,
                start_new_session=True,
                **({"cwd": cwd} if cwd is not None else {}),
            )

            self.__register_process(process.pid, _ManagedProcess(process))
            diagnostics.record("process.started", {"program": "restic", "pid": process.pid,
                               "command": next((part for part in cmd if part in {"backup", "restore", "check", "stats", "prune", "forget", "snapshots", "ls", "dump", "init", "unlock"}), "other")},
                               operation_uuid=operation_uuid)
            if callback is not None and callback_pid:
                callback(None, **{**callback_args, "pid": process.pid})

            if stdin_data is not None and process.stdin is not None:
                process.stdin.write(stdin_data)
                process.stdin.close()

            stderr_chunks = []
            stderr_thread = threading.Thread(
                target=self.__drain_binary_stream,
                args=(process.stderr, stderr_chunks, None, process.pid, operation_uuid),
                daemon=True,
            )
            stderr_thread.start()
            output = self.__monitor_output(
                process, callback, callback_args, callback_pid, callback_throttle, deadline, monitor_callback
            )

            return_code = process.wait()
            stderr_thread.join()
            stderr_output = self.__join_stream_chunks(stderr_chunks)

            if return_code != 0:
                status = parsers.backup(output) if return_code == 3 else {}
                raise ResticFailedError(
                    f"Restic failed with exit code {return_code}: {stderr_output}",
                    snapshot_id=status.get("snapshot_id") if isinstance(status, dict) else None,
                )

        except FileNotFoundError as err:
            raise ResticBinaryNotFoundError(
                f"Restic binary not found at {self.binary_path}"
            ) from err
        finally:
            if process is not None:
                if process.poll() is None:
                    _ManagedProcess(process).terminate(self.terminate_grace)
                diagnostics.record("process.finished", {"program": "restic", "pid": process.pid,
                                   "exit_code": process.poll(), "duration_seconds": time.monotonic() - started},
                                   operation_uuid=operation_uuid)
                self.__unregister_process(process.pid)
                if callback is not None and callback_pid:
                    callback(None, **{**callback_args, "pid": None})
            if restic_env_context is not None:
                restic_env_context.__exit__(None, None, None)

        logging.debug(f"{len(output)} lines of output")
        logging.debug("Restic proccess exitted")
        return parser(output)

    def set_repository(self, repository):
        self._state.repository = repository

    @contextmanager
    def mount(self, snapshot_id, mountpoint, *, cancelled=lambda: False, timeout=120):
        """Linux on-demand snapshot access, retaining repository/SSH credentials until close."""
        mountpoint = Path(mountpoint)
        mountpoint.mkdir(mode=0o700)
        command = self.__make_command("mount", str(mountpoint), path_template="ids/%I", quiet=True)
        try:
            with self.__restic_env() as env, mounted_process(
                command, mountpoint, env=env, cancelled=lambda: self._cancelled() or cancelled(),
                timeout=min(self.timeout or timeout, timeout),
            ) as process:
                self.__register_process(process.pid, _ManagedProcess(process))
                try:
                    # Snapshot lookup can block on repository I/O. The supervised disk
                    # helper opens this path; never access it in the agent worker here.
                    yield mountpoint / "ids" / snapshot_id
                finally:
                    self.__unregister_process(process.pid)
        finally:
            if not is_mounted(mountpoint):
                mountpoint.rmdir()

    def backup(
        self,
        paths,
        exclude_patterns=None,
        tags=None,
        callback=None,
        callback_args=None,
        callback_pid=False,
        callback_throttle=None,
        cwd=None,
        parent=None,
        monitor_callback=None,
        force=False,
    ):
        cmd = self.__make_command("backup", paths, exclude=exclude_patterns, tag=tags, parent=parent, verbose=True, force=force)

        return self.__execute_command(
            cmd,
            callback=callback,
            callback_args=callback_args,
            callback_pid=callback_pid,
            callback_throttle=callback_throttle,
            parser=parsers.backup,
            cwd=cwd,
            monitor_callback=monitor_callback,
        )

    def backup_stdin(
        self,
        stdin_data,
        stdin_filename,
        tags=None,
        callback=None,
        callback_args=None,
        callback_pid=False,
        callback_throttle=None,
    ):
        cmd = self.__make_command(
            "backup",
            stdin=True,
            stdin_filename=stdin_filename,
            tag=tags,
        )

        return self.__execute_command(
            cmd,
            stdin_data=stdin_data,
            callback=callback,
            callback_args=callback_args,
            callback_pid=callback_pid,
            callback_throttle=callback_throttle,
            parser=parsers.backup,
        )

    def backup_stdin_from_command(
        self,
        command,
        stdin_filename,
        tags=None,
        callback=None,
        callback_args=None,
        callback_pid=False,
        callback_throttle=None,
        producer_stderr_callback=None,
    ):
        started = time.monotonic()
        operation_uuid = (callback_args or {}).get("operation_uuid") or getattr(getattr(callback, "__self__", None), "uuid", None)
        cmd = self.__make_command(
            "backup",
            stdin=True,
            stdin_filename=stdin_filename,
            tag=tags,
        )
        callback_args = dict(callback_args or {})
        deadline = self.__deadline()
        producer_process = None
        process = None
        producer_stderr_chunks = []
        producer_stderr_thread = None
        restic_env_context = None
        output = []

        try:
            try:
                producer_process = self.__popen(
                    [str(part) for part in command],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=os.environ.copy(),
                    start_new_session=True,
                )
            except FileNotFoundError as err:
                raise ResticFailedError(f"Backup source command not found: {command[0]}") from err

            try:
                restic_env_context = self.__restic_env()
                restic_env = restic_env_context.__enter__()
                process = self.__popen(
                    cmd,
                    stdin=producer_process.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    env=restic_env,
                    start_new_session=True,
                )
            except FileNotFoundError as err:
                _ManagedProcess(producer_process).terminate(self.terminate_grace)
                raise ResticBinaryNotFoundError(
                    f"Restic binary not found at {self.binary_path}"
                ) from err

            producer_process.stdout.close()
            self.__register_process(process.pid, _ManagedProcess(process, producer_process))
            diagnostics.record("process.started", {"program": "restic", "pid": process.pid,
                               "producer_pid": producer_process.pid, "producer": str(command[0])},
                               operation_uuid=operation_uuid)
            if callback is not None and callback_pid:
                callback(None, **{**callback_args, "pid": process.pid})

            restic_stderr_chunks = []
            restic_stderr_thread = threading.Thread(
                target=self.__drain_binary_stream,
                args=(process.stderr, restic_stderr_chunks, None, process.pid, operation_uuid),
                daemon=True,
            )
            restic_stderr_thread.start()
            producer_stderr_thread = threading.Thread(
                target=self.__drain_binary_stream,
                args=(producer_process.stderr, producer_stderr_chunks, producer_stderr_callback, producer_process.pid, operation_uuid),
                daemon=True,
            )
            producer_stderr_thread.start()

            output = self.__monitor_output(
                process, callback, callback_args, callback_pid, callback_throttle, deadline
            )

            return_code = process.wait()
            producer_was_terminated = False
            if producer_process.poll() is None:
                if return_code == 0:
                    try:
                        producer_process.wait(timeout=self._producer_exit_grace)
                    except subprocess.TimeoutExpired:
                        producer_was_terminated = True
                        _ManagedProcess(producer_process).terminate(self.terminate_grace)
                else:
                    producer_was_terminated = True
                    _ManagedProcess(producer_process).terminate(self.terminate_grace)
            producer_return_code = producer_process.poll()
            restic_stderr_thread.join()
            if producer_stderr_thread is not None:
                producer_stderr_thread.join()
            stderr_output = self.__join_stream_chunks(restic_stderr_chunks)
            producer_stderr_output = self.__join_stream_chunks(producer_stderr_chunks)
            backup_status = parsers.backup(output)
            snapshot_id = (
                backup_status.get("snapshot_id") if isinstance(backup_status, dict) else None
            )

            if producer_return_code is None:
                raise ResticFailedError(
                    "Backup source command did not terminate", snapshot_id=snapshot_id
                )

            if producer_return_code != 0 and not producer_was_terminated:
                error_parts = [f"Backup source command failed with exit code {producer_return_code}"]
                if producer_stderr_output.strip():
                    error_parts.append(producer_stderr_output.strip())
                if stderr_output.strip():
                    error_parts.append(f"Restic stderr: {stderr_output.strip()}")
                raise ResticFailedError(": ".join(error_parts), snapshot_id=snapshot_id)

            if return_code != 0:
                raise ResticFailedError(
                    f"Restic failed with exit code {return_code}: {stderr_output}",
                    snapshot_id=snapshot_id,
                )

            if producer_return_code != 0:
                raise ResticFailedError(
                    f"Backup source command failed with exit code {producer_return_code}",
                    snapshot_id=snapshot_id,
                )

        finally:
            if process is not None:
                if process.poll() is None:
                    _ManagedProcess(process, producer_process).terminate(self.terminate_grace)
            if producer_process is not None and producer_process.poll() is None:
                _ManagedProcess(producer_process).terminate(self.terminate_grace)
            if process is not None:
                self.__unregister_process(process.pid)
                diagnostics.record("process.finished", {"program": "restic", "pid": process.pid,
                                   "exit_code": process.poll(), "producer_exit_code": producer_process.poll() if producer_process else None,
                                   "duration_seconds": time.monotonic() - started}, operation_uuid=operation_uuid)
                if callback is not None and callback_pid:
                    callback(None, **{**callback_args, "pid": None})
            if restic_env_context is not None:
                restic_env_context.__exit__(None, None, None)

        logging.debug(f"{len(output)} lines of output")
        logging.debug("Restic proccess exitted")
        return backup_status

    def init(self):
        cmd = self.__make_command("init")
        result = self.__execute_command(cmd)
        self.repository.restic_id = result["id"]
        return self.repository.restic_id

    def cat_config(self):
        cmd = self.__make_command("cat", "config")
        return self.__execute_command(cmd)

    def key_add(self, new_password):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as password_file:
            password_file.write(str(new_password or ""))
            password_file.flush()
            cmd = self.__make_command("key", "add", new_password_file=password_file.name)
            return self.__execute_command(cmd)

    def key_list(self):
        cmd = self.__make_command("key", "list")
        return self.__execute_command(cmd)

    def key_remove(self, key_id):
        cmd = self.__make_command("key", "remove", str(key_id))
        return self.__execute_command(cmd)

    def stats(self):
        cmd = self.__make_command("stats", mode="raw-data")
        return {**self.__execute_command(cmd), "mode": "raw-data"}

    def check(self, read_data=False, read_data_subset=None):
        cmd = self.__make_command(
            "check",
            read_data=read_data,
            read_data_subset=None if read_data else read_data_subset,
        )
        return self.__execute_command(cmd)

    def snapshots(self, tags=None, paths=None):
        cmd = self.__make_command("snapshots", tag=tags, path=paths)
        return self.__execute_command(cmd)

    def ls(self, snapshot_id="latest", path=None, recursive=False):
        args = ["ls", snapshot_id]
        if path:
            args.append(path)

        cmd = self.__make_command(args, recursive=recursive)
        return self.__execute_command(cmd)

    def restore(
        self,
        snapshot_id,
        target,
        include_paths,
        callback=None,
        callback_args=None,
        callback_pid=False,
        callback_throttle=None,
        overwrite_policy="fail_if_exists",
    ):
        if not include_paths:
            raise ValueError("Restore requires at least one include path")
        overwrite = {
            "fail_if_exists": "never",
            "overwrite": "always",
        }.get(overwrite_policy)
        if overwrite is None:
            raise ValueError("Unsupported overwrite policy")

        cmd = self.__make_command(
            "restore",
            snapshot_id,
            target=target,
            include=include_paths,
            overwrite=overwrite,
        )
        return self.__execute_command(
            cmd,
            callback=callback,
            callback_args=callback_args,
            callback_pid=callback_pid,
            callback_throttle=callback_throttle,
        )

    def forget(
        self,
        keep_last=None,
        keep_hourly=None,
        keep_daily=None,
        keep_weekly=None,
        keep_monthly=None,
        keep_yearly=None,
        keep_tags=None,
        dry_run=False,
        prune=True,
    ):
        keep_tags = keep_tags or []
        prune = bool(prune) and not dry_run
        cmd = self.__make_command(
            "forget",
            keep_last=keep_last,
            keep_hourly=keep_hourly,
            keep_daily=keep_daily,
            keep_weekly=keep_weekly,
            keep_monthly=keep_monthly,
            keep_yearly=keep_yearly,
            keep_tag=keep_tags,
            dry_run=dry_run,
            prune=prune,
        )

        return self.__execute_command(cmd)

    def forget_snapshots(self, snapshot_ids, dry_run=False, prune=True):
        snapshot_ids = [str(snapshot_id) for snapshot_id in (snapshot_ids or []) if snapshot_id]
        if not snapshot_ids:
            return []

        prune = bool(prune) and not dry_run
        cmd = self.__make_command("forget", snapshot_ids, dry_run=dry_run, prune=prune)
        return self.__execute_command(cmd)

    def prune(self):
        cmd = self.__make_command("prune")
        return self.__execute_command(cmd)

    def unlock(self):
        cmd = self.__make_command("unlock")
        return self.__execute_command(cmd)

    def cancel_process(self, pid):
        with self._process_lock:
            process = self._processes.pop(pid, None)

        if process:
            terminate = getattr(process, "terminate", None)
            if terminate is not None:
                terminate(self.terminate_grace)
            else:
                process.kill()
            return True

        return False


class _ManagedProcess:
    def __init__(self, *processes):
        self._processes = [process for process in processes if process is not None]

    def kill(self):
        for process in self._processes:
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                if process.poll() is None:
                    process.kill()
            except OSError:
                process.kill()

    def terminate(self, grace):
        running = [process for process in self._processes if process.poll() is None]
        for process in running:
            try:
                os.killpg(process.pid, 15)
            except ProcessLookupError:
                if process.poll() is None:
                    terminate = getattr(process, "terminate", None)
                    if terminate is not None:
                        terminate()
                    else:
                        process.kill()
            except OSError:
                process.terminate()
        end = time.monotonic() + max(0, grace)
        while running and time.monotonic() < end:
            running = [process for process in running if process.poll() is None]
            if running:
                time.sleep(min(0.05, max(0, end - time.monotonic())))
        if running:
            _ManagedProcess(*running).kill()
        for process in running:
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                continue
            except TypeError:
                process.poll()
