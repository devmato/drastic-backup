"""Execute configured host-shell and Docker lifecycle hooks with task timeouts."""

from threading import Lock

from drastic_agent.config import DefaultConfig, env_int
from drastic_common.process import run_process


class AgentAction:
    _docker_timeout_lock = Lock()

    @staticmethod
    def command(report, command, **kwargs):
        report.log_message(f"Executing command {command} on agent...")
        output = run_process(
            ["/bin/sh", "-c", command],
            timeout=env_int("DRASTIC_TASK_TIMEOUT_SECONDS", DefaultConfig.TASK_TIMEOUT_SECONDS),
            cancelled=(lambda: False) if kwargs.get("hook") in {"end", "error"} else report.cancel_event.is_set,
            truncate_output_at=8192,
        )
        report.log_message(f"Command finished:\n {output}")

    @staticmethod
    def docker(report, docker_client, container, action, command, **kwargs):
        timeout = env_int("DRASTIC_TASK_TIMEOUT_SECONDS", DefaultConfig.TASK_TIMEOUT_SECONDS)
        api = getattr(docker_client, "api", None)
        # The SDK timeout belongs to the shared client, so hooks must not change it concurrently.
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
