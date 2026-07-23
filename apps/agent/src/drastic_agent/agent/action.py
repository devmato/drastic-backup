import os
import subprocess
from threading import Lock


def _env_int(name: str, default: int) -> int:
    try:
        parsed = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default

    return parsed if parsed > 0 else default


class AgentAction:
    _docker_timeout_lock = Lock()

    @staticmethod
    def command(report, command, **kwargs):
        report.log_message(f"Executing command {command} on agent...")
        cmd = subprocess.run(
            command,
            timeout=_env_int("DRASTIC_TASK_TIMEOUT_SECONDS", 600),
            shell=True,
            check=True,
            capture_output=True,
        )
        report.log_message(f"Command finished:\n {cmd.stdout.decode()}")

    @staticmethod
    def docker(report, docker_client, container, action, command, **kwargs):
        timeout = _env_int("DRASTIC_TASK_TIMEOUT_SECONDS", 600)
        api = getattr(docker_client, "api", None)
        with AgentAction._docker_timeout_lock:
            previous_timeout = getattr(api, "timeout", None)
            if api is not None:
                api.timeout = timeout

            try:
                docker_container = docker_client.containers.get(container)
                if action == "stop":
                    report.log_message(f"Stopping container {container}...")
                    docker_container.stop()

                elif action == "start":
                    report.log_message(f"Starting container {container}...")
                    docker_container.start()

                elif action == "command" and command:
                    report.log_message(f"Executing command {command} in container {container}")
                    result = docker_container.exec_run(command)
                    exit_code = getattr(
                        result,
                        "exit_code",
                        result[0] if isinstance(result, tuple) else None,
                    )
                    output = getattr(
                        result,
                        "output",
                        result[1] if isinstance(result, tuple) else b"",
                    )
                    if exit_code not in (None, 0):
                        if isinstance(output, bytes):
                            output = output.decode(errors="replace")
                        raise RuntimeError(
                            f"Docker command exited with code {exit_code}: {str(output).strip()}"
                        )
            finally:
                if api is not None:
                    api.timeout = previous_timeout
