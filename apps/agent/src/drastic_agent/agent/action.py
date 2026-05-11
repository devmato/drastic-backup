import os
import subprocess


def _env_int(name: str, default: int) -> int:
    try:
        parsed = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default

    return parsed if parsed > 0 else default


class AgentAction:
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

        docker_container = docker_client.containers.get(container)

        if action == "stop":
            report.log_message(f"Stopping container {container}...")
            docker_container.stop()

        elif action == "start":
            report.log_message(f"Starting container {container}...")
            docker_container.start()

        elif action == "command" and command:
            report.log_message(f"Executing command {command} in container {container}")
            docker_container.exec_run(command)
