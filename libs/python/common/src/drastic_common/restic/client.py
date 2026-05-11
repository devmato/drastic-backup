import logging
import os
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta

import drastic_common.restic.parsers as parsers
from drastic_common.restic.exceptions import ResticBinaryNotFoundError, ResticFailedError


class ResticApi:
    _processes = {}
    _process_lock = threading.Lock()

    def __init__(self, binary_path, repository=None):
        self._state = threading.local()
        self.binary_path = binary_path

        if repository:
            self.set_repository(repository)

    @property
    def repository(self):
        return getattr(self._state, "repository", None)

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

        return env

    def __register_process(self, pid, process):
        with self._process_lock:
            self._processes[pid] = process

    def __unregister_process(self, pid):
        with self._process_lock:
            self._processes.pop(pid, None)

    def __drain_binary_stream(self, stream, output_chunks):
        try:
            while True:
                try:
                    chunk = stream.read(64 * 1024)
                except TypeError:
                    chunk = stream.read()
                    if chunk:
                        output_chunks.append(chunk)
                    break
                if not chunk:
                    break
                output_chunks.append(chunk)
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
    ):
        logging.debug(f"Executing command: {cmd}")
        callback_args = dict(callback_args or {})

        try:
            process = self.__popen(
                cmd,
                stdin=subprocess.PIPE if stdin_data is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                env=self.__build_env(),
                start_new_session=True,
            )

            self.__register_process(process.pid, _ManagedProcess(process))

            if stdin_data is not None and process.stdin is not None:
                process.stdin.write(stdin_data)
                process.stdin.close()

            output = []
            stderr_chunks = []
            stderr_thread = threading.Thread(
                target=self.__drain_binary_stream,
                args=(process.stderr, stderr_chunks),
                daemon=True,
            )
            stderr_thread.start()
            last_callback_invoked = None

            while True:
                last_output = process.stdout.readline()

                if last_output == "" and process.poll() is not None:
                    break

                if last_output != "":
                    if callback is not None:
                        if (
                            callback_throttle is None
                            or last_callback_invoked is None
                            or datetime.now() - last_callback_invoked
                            >= timedelta(milliseconds=callback_throttle)
                        ):
                            if callback_pid:
                                callback_args["pid"] = process.pid

                            callback(last_output, **callback_args)
                            last_callback_invoked = datetime.now()

                    output.append(last_output)

            return_code = process.wait()
            stderr_thread.join()
            stderr_output = self.__join_stream_chunks(stderr_chunks)

            self.__unregister_process(process.pid)

            if return_code != 0:
                raise ResticFailedError(
                    f"Restic failed with exit code {return_code}: {stderr_output}"
                )

        except FileNotFoundError as err:
            raise ResticBinaryNotFoundError(
                f"Restic binary not found at {self.binary_path}"
            ) from err

        logging.debug(f"{len(output)} lines of output")
        logging.debug("Restic proccess exitted")
        return parser(output)

    def set_repository(self, repository):
        self._state.repository = repository

    def backup(
        self,
        paths,
        exclude_patterns=None,
        tags=None,
        callback=None,
        callback_args=None,
        callback_pid=False,
        callback_throttle=None,
    ):
        cmd = self.__make_command("backup", paths, exclude=exclude_patterns, tag=tags)

        return self.__execute_command(
            cmd,
            callback=callback,
            callback_args=callback_args,
            callback_pid=callback_pid,
            callback_throttle=callback_throttle,
            parser=parsers.backup,
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
    ):
        cmd = self.__make_command(
            "backup",
            stdin=True,
            stdin_filename=stdin_filename,
            tag=tags,
        )
        callback_args = dict(callback_args or {})
        producer_process = None
        process = None
        producer_stderr_chunks = []
        producer_stderr_thread = None
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
                process = self.__popen(
                    cmd,
                    stdin=producer_process.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    env=self.__build_env(),
                    start_new_session=True,
                )
            except FileNotFoundError as err:
                producer_process.kill()
                producer_process.wait()
                raise ResticBinaryNotFoundError(
                    f"Restic binary not found at {self.binary_path}"
                ) from err

            producer_process.stdout.close()
            self.__register_process(process.pid, _ManagedProcess(process, producer_process))

            restic_stderr_chunks = []
            restic_stderr_thread = threading.Thread(
                target=self.__drain_binary_stream,
                args=(process.stderr, restic_stderr_chunks),
                daemon=True,
            )
            restic_stderr_thread.start()
            producer_stderr_thread = threading.Thread(
                target=self.__drain_binary_stream,
                args=(producer_process.stderr, producer_stderr_chunks),
                daemon=True,
            )
            producer_stderr_thread.start()

            last_callback_invoked = None

            while True:
                last_output = process.stdout.readline()

                if last_output == "" and process.poll() is not None:
                    break

                if last_output != "":
                    if callback is not None:
                        if (
                            callback_throttle is None
                            or last_callback_invoked is None
                            or datetime.now() - last_callback_invoked
                            >= timedelta(milliseconds=callback_throttle)
                        ):
                            if callback_pid:
                                callback_args["pid"] = process.pid

                            callback(last_output, **callback_args)
                            last_callback_invoked = datetime.now()

                    output.append(last_output)

            return_code = process.wait()
            producer_return_code = producer_process.wait()
            restic_stderr_thread.join()
            if producer_stderr_thread is not None:
                producer_stderr_thread.join()
            stderr_output = self.__join_stream_chunks(restic_stderr_chunks)
            producer_stderr_output = self.__join_stream_chunks(producer_stderr_chunks)

            if producer_return_code != 0:
                error_parts = [f"Backup source command failed with exit code {producer_return_code}"]
                if producer_stderr_output.strip():
                    error_parts.append(producer_stderr_output.strip())
                if stderr_output.strip():
                    error_parts.append(f"Restic stderr: {stderr_output.strip()}")
                raise ResticFailedError(": ".join(error_parts))

            if return_code != 0:
                raise ResticFailedError(
                    f"Restic failed with exit code {return_code}: {stderr_output}"
                )

        finally:
            if process is not None:
                self.__unregister_process(process.pid)
                if process.poll() is None:
                    process.kill()
                    process.wait()
            if producer_process is not None and producer_process.poll() is None:
                producer_process.kill()
                producer_process.wait()

        logging.debug(f"{len(output)} lines of output")
        logging.debug("Restic proccess exitted")
        return parsers.backup(output)

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
        cmd = self.__make_command("stats")
        return self.__execute_command(cmd)

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
    ):
        if not include_paths:
            raise ValueError("Restore requires at least one include path")

        cmd = self.__make_command(
            "restore",
            snapshot_id,
            target=target,
            include=include_paths,
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

    def unlock(self):
        cmd = self.__make_command("unlock")
        return self.__execute_command(cmd)

    def cancel_process(self, pid):
        with self._process_lock:
            process = self._processes.pop(pid, None)

        if process:
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
                continue
            except OSError:
                process.kill()
