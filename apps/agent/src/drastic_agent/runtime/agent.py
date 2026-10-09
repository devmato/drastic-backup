"""Compose the agent runtime and expose its validated command entry points."""

import configparser
import logging
import os
import platform
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, RLock
from time import monotonic, sleep

import requests
from docker import DockerClient
from docker.errors import DockerException
from marshmallow import ValidationError
from socketio.exceptions import ConnectionError

from drastic_agent.agent.action import AgentAction
from drastic_agent.agent.enums import AgentReportState, AgentReportType
from drastic_agent.agent.exceptions import AgentExeption
from drastic_agent.agent.report import AgentReport
from drastic_agent.config import DefaultConfig, env_flag, env_int, env_value
from drastic_agent.integrations.restic_binary import ensure_binary
from drastic_agent.jobs.context import BackupContext
from drastic_agent.jobs.registry import get_job_handler
from drastic_agent.runtime import commands, connection, reporting, scheduler, updates
from drastic_agent.runtime.execution import ExecutionManager
from drastic_agent.services import repository_access
from drastic_agent.services import secrets as repository_secrets_service
from drastic_agent.services.connections import ConnectionSettings
from drastic_agent.services.restore import RestoreContext, RestoreService
from drastic_agent.services.retention import RetentionService
from drastic_agent.storage.database import (
    actions,
    agent_operation_artifacts,
    agent_operations,
    db,
    jobs,
    repositories,
    repository_secrets,
    retentions,
    schedule_runs,
    schedules,
    truenas_snapshots,
)
from drastic_agent.storage.database import (
    agent as agent_settings,
)
from drastic_agent.storage.sync import replace_configuration
from drastic_common import diagnostics
from drastic_common.agent.commands import (
    AGENT_PROTOCOL_VERSION,
    ASYNC_AGENT_COMMANDS,
    AgentCommandName,
    AgentCommandRequestSchema,
    AgentDebugRequestSchema,
)
from drastic_common.agent.enums import AgentJobActionModule
from drastic_common.restic import RESTIC_VERSION, ResticApi
from drastic_common.restic.exceptions import ResticError
from drastic_common.restic.repository import ResticRepository
from drastic_common.scheduling import TimingSchema, preview
from drastic_common.secret_envelope import generate_agent_keypair
from drastic_common.ssh_keys import generate_ssh_keypair
from drastic_common.version import get_version as agent_version

AGENT_INSTALL_ROOT = Path("/opt/drastic-agent")
AGENT_COMMAND = AGENT_INSTALL_ROOT / "bin/drastic-agent"
AGENT_UPDATE_UNIT = "drastic-agent-update.service"
_COMMAND_ADMISSION_LOCK = RLock()


def _agent_data_dir() -> str:
    return env_value("DRASTIC_AGENT_DATA_DIR", DefaultConfig.AGENT_DATA_DIR)


def _agent_data_path(*parts: str) -> str:
    return os.path.join(_agent_data_dir(), *parts)


def _ensure_agent_data_dir() -> str:
    data_dir = _agent_data_dir()
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    try:
        os.chmod(data_dir, 0o700)
    except PermissionError:
        logging.warning("Could not set permissions on agent data directory %s", data_dir)
    return data_dir


def _is_ssh_repository_location(location) -> bool:
    normalized = str(location or "").strip().lower()
    return normalized.startswith("sftp:") or normalized.startswith("ssh:")


def _encode_config_secret(value: str) -> str:
    return value.replace("\n", "\\n")


def _decode_config_secret(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("\\n", "\n")


class Agent:
    """Own process resources; delegate durable workflows to runtime modules and services."""

    identifier = None
    client = None
    __docker_client = None
    __secret = None
    __resticapi = None
    __config = None
    __not_configured_logged = False
    __managed_repo_rewrite_warning_logged = False

    def __init__(self, server=None, identifier=None, secret=None):
        self.identifier = identifier
        self.client = None
        self.__secret = secret
        self.__server = server
        self.__config = None
        self.__not_configured_logged = False
        self.__managed_repo_rewrite_warning_logged = False
        self.__next_register_attempt_at = datetime.min
        self.__next_reconnect_attempt_at = datetime.min
        self.__register_retry_interval = timedelta(seconds=15)
        self.__reconnect_retry_interval = timedelta(seconds=15)
        self.__schedule_run_slots = {}
        self.__repository_passwords = {}
        self.__secret_values = {}
        self.__shutdown_event = Event()
        self.__connect_requested = Event()
        self.__connect_requested.set()
        self.__sync_pending = Event()
        self.__execution = ExecutionManager()
        self._diagnostic_report = AgentReport.command_report(data={"diagnostic": True})
        from drastic_agent.services.diagnostics import install_log
        install_log(_ensure_agent_data_dir())

        self.__check_restic_binary()

        if os.path.exists("/var/run/docker.sock"):
            try:
                self.__docker_client = DockerClient(base_url="unix://var/run/docker.sock")
            except DockerException:
                pass

        self.__config = configparser.ConfigParser()

    @property
    def os(self) -> str:
        return platform.system()

    @property
    def os_clean(self) -> str:
        s = self.os.lower()
        if s.startswith("linux"):
            return "linux"
        if s.startswith("darwin") or s in ("mac", "macos"):
            return "darwin"
        if s.startswith("win"):
            return "windows"
        if s.startswith("freebsd"):
            return "freebsd"
        if s.startswith("openbsd"):
            return "openbsd"
        if s.startswith("netbsd"):
            return "netbsd"
        if s.startswith("dragonfly"):
            return "dragonfly"
        if s in ("sunos", "solaris"):
            return "solaris"
        if s.startswith("aix"):
            return "aix"
        return s

    @property
    def arch(self) -> str:
        m = platform.machine().lower()
        m = re.sub(r"[^a-z0-9_+-]", "", m)
        if m in ("x86_64", "amd64"):
            return "amd64"
        if m in ("i386", "i486", "i586", "i686", "x86"):
            return "386"
        if m in ("aarch64", "arm64"):
            return "arm64"
        if m.startswith("armv") or m == "arm":
            return "arm"
        return m

    @property
    def hostname(self):
        return platform.node()

    @property
    def platform(self) -> str:
        return str(os.environ.get("DRASTIC_AGENT_PLATFORM") or self.os_clean)

    @property
    def deployment(self) -> str:
        configured_deployment = str(os.environ.get("DRASTIC_AGENT_DEPLOYMENT") or "").strip()
        if configured_deployment:
            return configured_deployment
        return "docker" if os.path.exists("/.dockerenv") else "native"

    @property
    def install_type(self) -> str:
        if self.deployment == "docker":
            return "docker"
        install_source = str(os.environ.get("DRASTIC_AGENT_INSTALL_SOURCE") or "").strip().lower()
        if install_source in {"release", "git"}:
            return install_source
        return "manual"

    @property
    def version(self):
        return agent_version()

    def __check_restic_binary(self):
        binary_path = ensure_binary(_agent_data_path("bin"), self.os_clean, self.arch)
        shutdown_event = getattr(self, "_Agent__shutdown_event", None)
        if shutdown_event is None:
            shutdown_event = Event()
            self.__shutdown_event = shutdown_event
        self.__resticapi = ResticApi(
            binary_path=binary_path,
            cancellation_event=shutdown_event,
            timeout=env_int("DRASTIC_RESTIC_TIMEOUT_SECONDS", DefaultConfig.RESTIC_TIMEOUT_SECONDS),
        )

    """ Start job & report scheduler (Blocking) """

    def __start_scheduler(self):
        sleep_time = 0.1
        next_sync_attempt = 0

        while not self.__shutdown_event.wait(sleep_time):
            now = datetime.now()
            self._debug_scheduler = {"at": datetime.now(timezone.utc).isoformat(), "phase": "maintenance"}

            if monotonic() >= getattr(self, "_next_restore_cleanup", 0):
                self._next_restore_cleanup = monotonic() + 60
                try:
                    from drastic_agent.services.proxmox_restore import cleanup_workspaces

                    cleanup_workspaces()
                except Exception:
                    logging.exception("Restore workspace cleanup failed")

            if self.connected and self.__sync_pending.is_set() and monotonic() >= next_sync_attempt:
                self.__sync_pending.clear()
                self._debug_scheduler["phase"] = "sync"
                if self.cmd_sync().state == AgentReportState.failed:
                    self.__sync_pending.set()
                    next_sync_attempt = monotonic() + 15
                continue

            # Online schedules must use refreshed config; offline schedules keep using the cache.
            if not self.__check_update() and not (self.connected and self.__sync_pending.is_set()):
                self._debug_scheduler["phase"] = "schedules"
                self.__run_due_schedules(now)
                if monotonic() >= getattr(self, "_next_retention_retry", 0):
                    self._next_retention_retry = monotonic() + 60
                    self.__retry_retention()

            # Process tasks that require server connection
            if self.connected:
                self._debug_scheduler["phase"] = "reports"
                self.__flush_report_queue()
                if monotonic() >= getattr(self, "_next_diagnostics", 0):
                    self._next_diagnostics = monotonic() + 10
                    try:
                        self._debug_scheduler["phase"] = "diagnostics"
                        self.__sample_diagnostics()
                    except Exception as exc:
                        diagnostics.local_event("sampling.failed", {"error": type(exc).__name__, "message": str(exc)})
                        logging.warning("Diagnostic sampling failed")
            else:
                self._debug_scheduler["phase"] = "connection"
                self.__maintain_server_connection(now)

    def __check_update(self, *, startup=False):
        manager = self.__execution_manager()
        if self.install_type not in {"git", "docker"}:
            return False
        if monotonic() >= getattr(self, "_Agent__next_update_check_at", 0):
            self.__next_update_check_at = monotonic() + 5
            self.__read_update_reports()
            return updates.check_state(self.install_type, manager, startup=startup,
                                       install_root=AGENT_INSTALL_ROOT, unit=AGENT_UPDATE_UNIT)
        return manager.maintenance

    def __read_update_reports(self):
        updates.read_reports(_agent_data_dir(), AGENT_INSTALL_ROOT, agent_operations)


    def __run_due_schedules(self, now):
        self.__schedule_run_slots = getattr(self, "_Agent__schedule_run_slots", {})
        scheduler.run_due(
            now, jobs=jobs, schedules=schedules, runs=schedule_runs,
            slots=self.__schedule_run_slots, submit=self.__execution_manager().submit,
            run_job=self.cmd_run_job,
        )

    def __recover_schedule_runs(self):
        scheduler.recover_runs(schedule_runs)

    def __prune_schedule_runs(self, now=None):
        scheduler.prune_runs(schedule_runs, now)

    def __flush_report_queue(self):
        reporting.flush(self.__send_report)

    def __sample_diagnostics(self):
        if self._diagnostic_report not in AgentReport.pending_reports:
            AgentReport.pending_reports.append(self._diagnostic_report)
        self._diagnostic_report.sent = False  # Poll consent through the normal report response, even when idle.
        if diagnostics.active():
            with AgentReport._registry_lock:
                operations = list(AgentReport.running_operations.values())
            manager = self.__execution_manager()
            with manager._lock:
                execution = {"admitted": manager._admitted, "max_workers": manager.max_workers,
                             "max_pending": manager.max_pending, "maintenance": manager._maintenance,
                             "resources": sorted(str(item) for item in manager._resources)}
            for operation in operations:
                with operation._lock:
                    operation.diagnostic_at = datetime.now(timezone.utc)
                    operation.sent = False
            processes = ResticApi.diagnostic_processes()
            diagnostics.record("agent.sample", {
                "version": self.version, "protocol_version": AGENT_PROTOCOL_VERSION,
                "agent_source": diagnostics.source_fingerprint("drastic_agent"),
                "common_source": diagnostics.source_fingerprint("drastic_common"),
                "restic_version": RESTIC_VERSION, "platform": self.platform,
                "execution": execution, "running_operations": [operation.uuid for operation in operations],
                "pending_reports": len(AgentReport.pending_reports),
                "finished_reports": len(AgentReport.finished_reports),
                "dropped_events": self._diagnostic_report.data.get("dropped_events", 0),
                "system": diagnostics.system_snapshot(processes),
            })

    def __maintain_server_connection(self, now):
        if self.__shutdown_event.is_set() or self.connected:
            return

        if not self.configured:
            if self.can_register and now >= self.__next_register_attempt_at:
                self.__next_register_attempt_at = now + self.__register_retry_interval
                if self.register(
                    self.__server, self.registration_username, self.registration_password
                ):
                    self.__next_reconnect_attempt_at = now
                    self.__connect()
                return

            if not self.__not_configured_logged:
                logging.info(
                    "Agent is not configured yet. Waiting for registration credentials or a saved agent config."
                )
                self.__not_configured_logged = True
            return

        self.__not_configured_logged = False

        # Socket.IO owns transport reconnects; only bootstrap/server disconnects come here.
        if not self.__connect_requested.is_set() or now < self.__next_reconnect_attempt_at:
            return

        self.__next_reconnect_attempt_at = now + self.__reconnect_retry_interval
        self.__connect()

    """ Send request to server """

    def __send_request(self, __action, **kwargs):
        return connection.request(self.client, __action, kwargs, timeout=env_int(
            "DRASTIC_COMMAND_TIMEOUT_SECONDS", DefaultConfig.COMMAND_TIMEOUT_SECONDS,
        ))

    """ Send report to server """

    def __send_report(self, report):
        self._debug_report = dict(getattr(self, "_debug_report", {}))
        return reporting.send(report, request=self.__send_request, debug=self._debug_report)

    """ Setup repository for use with restic binary """

    def __set_repository(self, repository_id):
        # Load repository
        repository = repositories.find_one(id=repository_id)

        # Cancel if repository not found
        if not repository:
            raise AgentExeption(
                f"Repository with id {repository_id} not found in agent repositories"
            )

        self.configure_repository(repository)
        return repository

    def configure_repository(self, repository):
        location, env = self.__repository_location_env(repository)
        password = self.__repository_password(repository, location, env)

        # Setup repository for usage with restic
        self.__resticapi.set_repository(
            ResticRepository(
                location=location,
                password=password,
                env=env,
                ssh_private_key=self.__repository_ssh_private_key(repository),
                ssh_known_hosts_path=self.__ssh_known_hosts_path(),
            )
        )
        return repository

    def __ssh_known_hosts_path(self):
        return _agent_data_path("ssh", "known_hosts")

    def __ssh_identity_private_key_path(self):
        return _agent_data_path("ssh", "identity")

    def __ssh_identity_public_key_path(self):
        return _agent_data_path("ssh", "identity.pub")

    def __ensure_ssh_keypair(self, rotate=False):
        ssh_dir = _agent_data_path("ssh")
        private_key_path = self.__ssh_identity_private_key_path()
        public_key_path = self.__ssh_identity_public_key_path()

        if not rotate and os.path.exists(private_key_path) and os.path.exists(public_key_path):
            return

        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(ssh_dir, 0o700)
        except PermissionError:
            logging.warning("Could not set permissions on agent SSH directory %s", ssh_dir)

        private_key, public_key = generate_ssh_keypair()
        with open(private_key_path, "w", encoding="utf-8") as key_file:
            key_file.write(private_key)
            if not private_key.endswith("\n"):
                key_file.write("\n")
        with open(public_key_path, "w", encoding="utf-8") as key_file:
            key_file.write(public_key)
            if not public_key.endswith("\n"):
                key_file.write("\n")

        try:
            os.chmod(private_key_path, 0o600)
            os.chmod(public_key_path, 0o600)
        except PermissionError:
            logging.warning("Could not set permissions on agent SSH identity")

    def __agent_ssh_private_key(self):
        self.__ensure_ssh_keypair()
        try:
            with open(self.__ssh_identity_private_key_path(), encoding="utf-8") as key_file:
                return key_file.read()
        except OSError as exc:
            raise AgentExeption(f"Agent SSH private key could not be loaded: {exc}") from exc

    def __agent_ssh_public_key(self):
        self.__ensure_ssh_keypair()
        try:
            with open(self.__ssh_identity_public_key_path(), encoding="utf-8") as key_file:
                return key_file.read().strip()
        except OSError as exc:
            raise AgentExeption(f"Agent SSH public key could not be loaded: {exc}") from exc

    def __repository_ssh_private_key(self, repository):
        if not _is_ssh_repository_location(repository.get("location")):
            return None
        return self.__agent_ssh_private_key()

    def __repository_location_env(self, repository):
        return repository_access.location_environment(
            repository, identifier=self.identifier, server_url=self.__server,
            agent_secret=self.__secret, rewrite_managed=self.managed_repo_rewrite_enabled,
        )

    def __repository_password(self, repository, location, env):
        password = self.__repository_agent_password(repository)
        if password:
            return password

        return self.__provision_agent_repository_password(
            repository=repository,
            location=location,
            env=env,
            initialize=True,
        )

    def __repository_password_cache(self):
        if not hasattr(self, "_Agent__repository_passwords"):
            self.__repository_passwords = {}
        return self.__repository_passwords

    def __secret_value_cache(self):
        if not hasattr(self, "_Agent__secret_values"):
            self.__secret_values = {}
        return self.__secret_values

    def __repository_agent_password(self, repository):
        return repository_secrets_service.agent_password(repository, self.private_key, self.__repository_password_cache())

    def __repository_recovery_password(self, repository):
        return repository_secrets_service.recovery_password(
            repository, self.private_key, self.__secret_value_cache(), self.__send_request,
        )

    def __strip_repository_secret_fields(self, repository):
        sanitized = dict(repository)
        sanitized.pop("encrypted_recovery_key", None)
        return sanitized

    def __store_repository_agent_key(self, repository_id, password):
        repository_secrets_service.store_agent_key(
            repository_id, password, public_key=self.public_key, repositories=repositories,
            json_type=db.types.json, request=self.__send_request,
        )

    def __clear_local_repository_secret(self, repository_id):
        try:
            repository_secrets.delete(repository_id=repository_id)
        except Exception:
            pass

    def __provision_agent_repository_password(self, repository, location, env, initialize):
        repository_id = repository.get("id")
        agent_password = self.__repository_password_cache().get(repository_id) or secrets.token_urlsafe(32)
        recovery_password = self.__repository_recovery_password(repository)

        repository_access.provision_key(
            self.__resticapi,
            ResticRepository(
                location=location,
                password=recovery_password,
                env=env,
                ssh_private_key=self.__repository_ssh_private_key(repository),
                ssh_known_hosts_path=self.__ssh_known_hosts_path(),
            ),
            agent_password, repository_id=repository_id, initialize=initialize,
        )

        self.__repository_password_cache()[repository_id] = agent_password
        try:
            self.__store_repository_agent_key(repository_id, agent_password)
        except AgentExeption as exc:
            logging.warning(str(exc))
        self.__clear_local_repository_secret(repository_id)
        self.__resticapi.set_repository(
            ResticRepository(
                location=location,
                password=agent_password,
                env=env,
                ssh_private_key=self.__repository_ssh_private_key(repository),
                ssh_known_hosts_path=self.__ssh_known_hosts_path(),
            )
        )
        return agent_password

    def initialize_repository_with_recovery(self, repository):
        location, env = self.__repository_location_env(repository)
        return self.__provision_agent_repository_password(
            repository=repository,
            location=location,
            env=env,
            initialize=True,
        )

    def recover_repository_access_with_recovery(self, repository):
        location, env = self.__repository_location_env(repository)
        return self.__provision_agent_repository_password(
            repository=repository,
            location=location,
            env=env,
            initialize=False,
        )

    @property
    def resticapi(self):
        return self.__resticapi

    def set_repository(self, repository_id):
        return self.__set_repository(repository_id)

    def execute_actions(self, actions, report):
        return self.__execute_actions(actions=actions, report=report)

    def __wait_for_report_pid(self, report):
        timeout = env_int(
            "DRASTIC_CANCEL_WAIT_TIMEOUT_SECONDS", DefaultConfig.CANCEL_WAIT_TIMEOUT_SECONDS
        )
        deadline = monotonic() + timeout
        while "pid" not in report.data and monotonic() < deadline:
            sleep(1)

        return report.data.get("pid")

    def __create_client(self):
        return connection.create_client(execute=self.__handle_execute_command,
                                        sync_pending=self.__sync_pending, connect_requested=self.__connect_requested)

    def __run_command_async(self, command_name, command_args):
        return commands.submit(
            command_name, command_args, execute=getattr(self, f"cmd_{command_name}"),
            manager=self.__execution_manager(), operations=agent_operations,
        )

    def __validate_run_job_admission(self, command_args):
        return commands.validate_job(command_args, jobs=jobs, repositories=repositories, retentions=retentions)

    def __execution_manager(self):
        if not hasattr(self, "_Agent__execution"):
            self.__execution = ExecutionManager()
        return self.__execution

    def __handle_execute_command(self, data):
        try:
            command_request = AgentCommandRequestSchema().load(data)
        except ValidationError as exc:
            report = AgentReport.command_report()
            report.log_message(
                f"Invalid agent command payload: {exc.messages}",
                final_state=AgentReportState.failed,
            )
            return report.finish()

        command = command_request["command"]
        if command in ASYNC_AGENT_COMMANDS or command == AgentCommandName.get_operation_status:
            # Only short admission/status paths share the cancellation fence.
            # Synchronous reads, sync and process termination must never hold it.
            with _COMMAND_ADMISSION_LOCK:
                return self.__execute_command(command_request)
        return self.__execute_command(command_request)

    def __execute_command(self, command_request):
        command = command_request["command"]
        command_args = dict(command_request.get("args") or {})
        if command == AgentCommandName.debug_state:
            # This path must not log via, finish, or lock an existing operation report.
            response = AgentReport.command_report()
            try:
                args = AgentDebugRequestSchema().load(command_args)
                self.__configure_diagnostics(args["enabled"])
                if args["enabled"] and args["section"]:
                    from drastic_agent.services.diagnostics import snapshot
                    response.data = snapshot(self, _agent_data_dir(), args["section"], args["limit"])
                else:
                    response.data = {"enabled": args["enabled"]}
            except Exception as exc:
                response.final_state = AgentReportState.failed
                response.data = {"error": type(exc).__name__}
            response.ended = datetime.now(timezone.utc)
            return response
        return commands.execute(
            command_request, find_handler=lambda name: getattr(self, f"cmd_{name}", None),
            submit_async=self.__run_command_async, validate_job=self.__validate_run_job_admission,
            operations=agent_operations,
        )

    def __connect(self):
        if self.__shutdown_event.is_set():
            return False
        if not self.configured:
            if not self.__not_configured_logged:
                logging.info(
                    "Agent is not configured yet. Waiting for registration credentials or a saved agent config."
                )
                self.__not_configured_logged = True
            return False

        if self.connected:
            return True

        if not self.__connect_requested.is_set():
            return False
        self.__connect_requested.clear()

        if self.client:
            try:
                self.client.shutdown()
            except Exception:
                pass
            self.client = None

        client = self.client = self.__create_client()

        try:
            client.connect(self.__server, auth=lambda: self.authdata, namespaces=["/agent"])
            if self.__shutdown_event.is_set():
                client.shutdown()
                return False
            return True
        except ConnectionError as exc:
            logging.warning(f"Could not connect to server {self.__server}: {exc}")
        except Exception as exc:
            logging.warning(f"Unexpected agent connection error: {exc}")

        try:
            client.shutdown()
        except Exception:
            pass
        self.client = None
        self.__connect_requested.set()

        return False

    """ Execute agent actions """

    def __execute_actions(self, actions, report):
        log_message = True
        for action in actions:
            # Log hook name on first loop run
            if log_message:
                report.log_message(f"Executing actions on {action['hook']}:")
                log_message = False

            module_name = action.get("module")
            try:
                module = AgentJobActionModule[module_name].name
            except (KeyError, TypeError):
                report.log_message(
                    f"Action module {module_name} not supported",
                    final_state=AgentReportState.warning,
                )
                continue

            # Check if AgentAction has appropriate methode
            if hasattr(AgentAction, module):
                try:
                    # Execute action
                    getattr(AgentAction, module)(
                        report=report, docker_client=self.__docker_client, **action["data"]
                    )
                except Exception as e:
                    # Set warning state and log message on error
                    report.log_message(
                        f"Failed to execute {module} action: {e}",
                        final_state=AgentReportState.warning,
                    )

    """ Init configuration """

    def init_config(self):
        _ensure_agent_data_dir()
        self.__config.read(_agent_data_path("config.ini"))
        self.__ensure_ssh_keypair()

        env_server = os.getenv("DRASTIC_SERVER")
        env_username = os.getenv("DRASTIC_USER")
        env_password = os.getenv("DRASTIC_PASSWORD")

        # Set server url from environment variable
        if env_server:
            self.__config["SERVER"] = {"url": env_server}

        configured_server = self.__config.get("SERVER", "url", fallback=env_server)

        # Register new agent if no agent data is present
        if "AGENT" not in self.__config and env_username and env_password:
            self.register(configured_server, env_username, env_password)

        self.__server = configured_server
        self.identifier = self.__config.get("AGENT", "identifier", fallback=None)
        self.__secret = self.__config.get("AGENT", "secret", fallback=None)
        diagnostics.remember_secrets({"agent_secret": self.__secret})

        if "AGENT" in self.__config and not self.__config.get("AGENT", "private_key", fallback=None):
            private_key, public_key = generate_agent_keypair()
            self.__config["AGENT"]["private_key"] = _encode_config_secret(private_key)
            self.__config["AGENT"]["public_key"] = _encode_config_secret(public_key)
            self.save_config()

        if self.configured:
            self.__not_configured_logged = False

    def register(self, server, username, password):
        if not server:
            logging.error("Agent registration requires DRASTIC_SERVER or a saved server URL")
            return False

        self.__config["SERVER"] = {"url": server}
        self.__server = server

        logging.info(f"Attempting new agent registration for user {username}")
        private_key, public_key = generate_agent_keypair()
        try:
            response = requests.post(
                f"{self.__config['SERVER']['url']}/api/agents/register",
                json={
                    "username": username,
                    "password": password,
                    "hostname": self.hostname,
                    "os": self.os,
                    "version": self.version,
                    "platform": self.platform,
                    "deployment": self.deployment,
                    "install_type": self.install_type,
                    "public_key": public_key,
                    "ssh_public_key": self.ssh_public_key,
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            logging.error(f"Agent registration failed {os.linesep}{exc}")
            return False

        if response.status_code in {200, 201}:
            agentdata = response.json()
            self.__config["AGENT"] = {
                "identifier": str(agentdata["identifier"]),
                "secret": agentdata["secret"],
                "private_key": _encode_config_secret(private_key),
                "public_key": _encode_config_secret(public_key),
            }
            self.identifier = str(agentdata["identifier"])
            self.__secret = agentdata["secret"]
            self.__not_configured_logged = False
            self.save_config()
            logging.info(
                f"Agent registration successful! {os.linesep} Identifier: {agentdata['identifier']}"
            )
            return True
        else:
            logging.error(
                f"Agent registration failed {os.linesep}"
                f"Server responded with status code: {response.status_code}{os.linesep}"
                f"Response body: {response.text}"
            )
            return False

    """ Startup agent """

    def startup(self):
        if self.managed_repo_rewrite_enabled and self.env_name != "dev":
            self.__warn_managed_repo_rewrite_enabled()

        # Bound terminal schedule history without discarding interrupted runs.
        self.__prune_schedule_runs()
        self.__recover_schedule_runs()
        AgentReport.recover_interrupted()
        AgentReport.load_queue()
        from drastic_agent.services.proxmox_restore import cleanup_workspaces

        cleanup_workspaces(all_workspaces=True)

        from drastic_agent.jobs.truenas_backup import recover_snapshots

        recover_snapshots(self)

        from drastic_agent.jobs.proxmox_backup import recover_snapshots as recover_proxmox_snapshots

        recover_proxmox_snapshots()

        # The installer still performs startup checks after restarting this service.
        if self.install_type in {"git", "docker"}:
            self.__check_update(startup=True)

        # Attempt registration/connection to server
        self.__maintain_server_connection(datetime.now())

        # Start job & report scheduler (Blocking)
        self.__start_scheduler()

    def __warn_managed_repo_rewrite_enabled(self):
        if self.__managed_repo_rewrite_warning_logged:
            return

        logging.warning(
            "Managed repository URL rewrite is enabled outside dev mode. "
            "Set DRASTIC_AGENT_REWRITE_MANAGED_REPO_URLS only for exceptional setups."
        )
        self.__managed_repo_rewrite_warning_logged = True

    """ Shutdown agent """

    def shutdown(self):
        logging.info("Shutting down...")

        if hasattr(self, "_Agent__shutdown_event"):
            self.__shutdown_event.set()
        AgentReport.cancel_running()

        if self.client:
            try:
                self.client.shutdown()
            except Exception:
                pass

        if hasattr(self, "_Agent__execution"):
            self.__execution.shutdown(wait=True)

        from drastic_agent.services.proxmox_restore import cleanup_workspaces

        cleanup_workspaces(all_workspaces=True)

        # Finalize any operations which did not exit through their worker.
        AgentReport.save_queue()

    """ Save configuration """

    def save_config(self):
        logging.info("Saving config file")
        _ensure_agent_data_dir()
        config_path = _agent_data_path("config.ini")
        with open(config_path, "w") as configfile:
            self.__config.write(configfile)
        try:
            os.chmod(config_path, 0o600)
        except PermissionError:
            logging.warning("Could not set permissions on agent config file %s", config_path)

    @property
    def has_docker(self):
        return True if self.__docker_client else False

    @property
    def managed_repo_rewrite_enabled(self) -> bool:
        return env_flag(
            "DRASTIC_AGENT_REWRITE_MANAGED_REPO_URLS",
            DefaultConfig.AGENT_REWRITE_MANAGED_REPO_URLS,
        )

    @property
    def env_name(self) -> str:
        return env_value("DRASTIC_ENV", DefaultConfig.ENV).lower()

    @property
    def authdata(self):
        return {
            "agent_id": self.identifier,
            "agent_secret": self.__secret,
            "agent_os": self.os,
            "agent_hostname": self.hostname,
            "agent_version": self.version,
            "protocol_version": AGENT_PROTOCOL_VERSION,
            "connections": self.connection_status(),
            "agent_install_type": self.install_type,
            "agent_public_key": self.public_key,
            "agent_ssh_public_key": self.ssh_public_key,
        }

    @property
    def private_key(self):
        if self.__config is None or "AGENT" not in self.__config:
            return None
        return _decode_config_secret(self.__config.get("AGENT", "private_key", fallback=None))

    @property
    def public_key(self):
        if self.__config is None or "AGENT" not in self.__config:
            return None
        return _decode_config_secret(self.__config.get("AGENT", "public_key", fallback=None))

    @property
    def ssh_public_key(self):
        return self.__agent_ssh_public_key()

    @property
    def configured(self):
        return bool(self.__server and self.identifier and self.__secret)

    @property
    def registration_username(self):
        username = str(os.getenv("DRASTIC_USER") or "").strip()
        return username or None

    @property
    def registration_password(self):
        password = str(os.getenv("DRASTIC_PASSWORD") or "")
        return password or None

    @property
    def can_register(self):
        return bool(
            self.__server
            and self.registration_username
            and self.registration_password
            and not self.identifier
            and not self.__secret
        )

    @property
    def connected(self):
        if self.client and self.client.connected:
            return True
        return False

    def cmd_update(self):
        return updates.start(self.install_type, self.__execution_manager(), data_dir=_agent_data_dir(),
                             install_root=AGENT_INSTALL_ROOT, command=AGENT_COMMAND, unit=AGENT_UPDATE_UNIT)

    """ Get available docker container on this client """

    def cmd_get_containers(self):
        report = AgentReport(AgentReportType.command, data=[])

        if self.__docker_client:
            report.data = {
                "containers": [
                    container.name for container in self.__docker_client.containers.list(all=True)
                ]
            }

        return report.finish()

    """ Get directory listing (Used for backup job creation) """

    def cmd_get_dirlist(self, base_directory="/"):
        report = AgentReport.command_report(data={"dirlist": []})

        # Abort if path dont exist
        if not os.path.exists(base_directory):
            report.log_message(
                "Requested directory doesn't exist", final_state=AgentReportState.failed
            )
            return report.finish()

        # Abort if path is not directory
        if not os.path.isdir(base_directory):
            report.log_message(
                "Requested path is not a directory", final_state=AgentReportState.failed
            )
            return report.finish()

        directories = os.listdir(base_directory)

        for directory in directories:
            dir_path = os.path.join(base_directory, directory)
            if os.path.exists(dir_path):
                file = os.path.isfile(dir_path)
                size = round(os.path.getsize(dir_path) / (1024 * 1024), 2)
                readable = os.access(dir_path, os.R_OK)
                report.data["dirlist"].append(
                    {"name": directory, "file": file, "size": size, "readable": readable}
                )

        return report.finish()

    def __connection_settings(self):
        return ConnectionSettings(
            db, agent_settings, truenas_snapshots, private_key=self.private_key,
            public_key=self.public_key, identifier=self.identifier, platform=self.os_clean,
        )

    def get_proxmox_client(self):
        return self.__connection_settings().proxmox_client()

    def cmd_get_proxmox_settings(self):
        return self.__connection_settings().get_proxmox()

    def cmd_update_proxmox_settings(self, settings, encrypted_value=None):
        return self.__connection_settings().update_proxmox(settings, encrypted_value)

    def cmd_test_proxmox_settings(self, settings, encrypted_value=None):
        return self.__connection_settings().test_proxmox(settings, encrypted_value)

    def cmd_get_proxmox_guests(self):
        return self.__connection_settings().proxmox_guests()

    def connection_status(self):
        return self.__connection_settings().status()

    def get_truenas_client(self):
        return self.__connection_settings().truenas_client()

    def cmd_truenas_settings(self, action="get", settings=None, encrypted_value=None):
        return self.__connection_settings().truenas(action, settings, encrypted_value)

    def cmd_delete_connection(self, kind):
        return self.__connection_settings().delete(kind)

    """ Get restic repository stats """

    def cmd_get_repository_stats(self, repository_id):
        report = AgentReport(type=AgentReportType.repository_stats, repository_id=repository_id)
        report.log_message("Obtaining repository statistics...")

        try:
            self.__set_repository(repository_id)
        except AgentExeption as e:
            report.log_message(f"{e}", final_state=AgentReportState.failed)
            return report.finish()

        try:
            report.data["stats"] = self.__resticapi.stats()
            report.data["config"] = self.__resticapi.cat_config()
        except ResticError as e:
            report.log_message(
                f"Error on obtaining repository statistics: {e}",
                final_state=AgentReportState.failed,
            )

        return report.finish()

    def cmd_check_repository(self, repository_id, read_data_subset=None, operation_uuid=None):
        read_data = str(read_data_subset or "").strip() or None
        read_data_full = read_data == "100%"
        read_data_subset = None if read_data_full else read_data
        report = AgentReport.check_report(
            repository_id=repository_id,
            operation_uuid=operation_uuid,
            data={"read_data": read_data},
        )
        report.log_message("Starting repository check")
        if read_data_full:
            report.log_message("Reading all repository data")
        elif read_data_subset:
            report.log_message(f"Reading data subset: {read_data_subset}")

        try:
            self.__set_repository(repository_id)
            report.data["check"] = self.__resticapi.check(
                read_data=read_data_full,
                read_data_subset=read_data_subset,
            )
        except (ResticError, AgentExeption) as e:
            report.log_message(f"Repository check failed: {e}", final_state=AgentReportState.failed)
            return report.finish()

        report.log_message("Repository check finished successfully")
        return report.finish()

    """ Sync job & repository data with server """

    def cmd_sync(self):
        report = AgentReport.command_report()

        request = self.__send_request("sync")

        if not request.get("success"):
            report.log_message("Agent sync failed", final_state=AgentReportState.failed)
            return report.finish()

        try:
            server_data = request["result"]
            if "diagnostic_enabled" in server_data:
                self.__configure_diagnostics(server_data["diagnostic_enabled"])
            synced_repositories = [
                self.__strip_repository_secret_fields(repository)
                for repository in server_data["repositories"]
            ]

            replace_configuration(db, (
                    (repositories, synced_repositories),
                    (retentions, server_data["retentions"]),
                    (jobs, server_data["jobs"]),
                    (schedules, server_data["schedules"]),
                    (actions, server_data["actions"]),
                ), legacy_secrets=repository_secrets)
        except Exception as exc:
            logging.exception("Agent sync transaction failed")
            report.log_message(f"Agent sync failed: {exc}", final_state=AgentReportState.failed)
            self._debug_sync = {"at": datetime.now(timezone.utc).isoformat(), "success": False, "error": type(exc).__name__}
            return report.finish()

        self.__secret_value_cache().clear()
        for envelope in server_data.get("secret_envelopes") or []:
            try:
                self.__store_secret_envelope_value(envelope)
            except AgentExeption as exc:
                report.log_message(str(exc), final_state=AgentReportState.warning)

        self.__repository_password_cache().clear()
        for repository in synced_repositories:
            try:
                self.__repository_agent_password(repository)
            except AgentExeption as exc:
                report.log_message(str(exc), final_state=AgentReportState.warning)

        self._debug_sync = {"at": datetime.now(timezone.utc).isoformat(), "success": True}
        return report.finish()

    def __configure_diagnostics(self, enabled):
        diagnostics.configure(self._diagnostic_report if enabled else None)

    def cmd_reset_known_hosts(self):
        report = AgentReport.command_report()
        known_hosts_path = self.__ssh_known_hosts_path()
        try:
            if os.path.exists(known_hosts_path):
                os.remove(known_hosts_path)
                report.log_message("SSH known_hosts reset")
            else:
                report.log_message("SSH known_hosts was already empty")
        except Exception as exc:
            report.log_message(
                f"Resetting SSH known_hosts failed: {exc}",
                final_state=AgentReportState.failed,
            )
        return report.finish()

    def cmd_rotate_ssh_key(self):
        self.__ensure_ssh_keypair(rotate=True)
        public_key = self.__agent_ssh_public_key()
        report = AgentReport.command_report(data={"ssh_public_key": public_key})
        report.log_message("Agent SSH key rotated. Install the new public key on SSH targets before the next run.")
        return report.finish()

    def __store_secret_envelope_value(self, envelope):
        repository_secrets_service.store_secret_value(envelope, self.private_key, self.__secret_value_cache())

    """ Run backup job: returns None """

    def cmd_run_job(
        self,
        job_id,
        repository_id,
        retention_id=None,
        operation_uuid=None,
        run_options=None,
    ):
        if (run_options or {}).get("chain_run_id"):
            reason = self.__validate_run_job_admission({"job_id": job_id, "repository_id": repository_id,
                                                       "retention_id": retention_id, "run_options": run_options})
            if reason:
                report = AgentReport.backup_operation(job_id=job_id, repository_id=repository_id,
                                                      retention_id=retention_id, operation_uuid=operation_uuid)
                report.set_data("chain_run_id", run_options["chain_run_id"])
                report.set_data("start_skipped", True)
                report.set_data("start_skip_reason", reason)
                report.log_message(reason, final_state=AgentReportState.failed)
                return report.finish()
        job = jobs.find_one(id=job_id)
        repository = repositories.find_one(id=repository_id)

        if not job:
            report = AgentReport.command_report()
            report.log_message(
                f"Job with id {job_id} not found", final_state=AgentReportState.failed
            )
            return report.finish()

        if not repository:
            report = AgentReport.command_report()
            report.log_message(
                f"Repository with id {repository_id} not found",
                final_state=AgentReportState.failed,
            )
            return report.finish()

        try:
            handler = get_job_handler(
                agent=BackupContext(
                    identifier=self.identifier,
                    resticapi=self.resticapi,
                    set_repository=self.set_repository,
                    execute_actions=self.execute_actions,
                    initialize_repository_with_recovery=self.initialize_repository_with_recovery,
                    recover_repository_access_with_recovery=self.recover_repository_access_with_recovery,
                    cmd_run_retention=self.cmd_run_retention,
                    cmd_get_repository_stats=self.cmd_get_repository_stats,
                    get_proxmox_client=self.get_proxmox_client,
                    get_truenas_client=self.get_truenas_client,
                ),
                job=job,
                repository_id=repository_id,
                retention_id=retention_id,
                operation_uuid=operation_uuid,
                run_options=run_options or {},
            )
        except ValueError as exc:
            report = AgentReport.command_report()
            report.log_message(str(exc), final_state=AgentReportState.failed)
            return report.finish()

        return handler.run()

    def cmd_get_operation_status(self, operation_uuid):
        operation = agent_operations.find_one(uuid=operation_uuid)
        return AgentReport.command_report(data={
            "known": bool(operation),
            "operation_state": operation.get("state") if operation else None,
        }).finish()

    def cmd_preview_schedule(self, timing):
        report = AgentReport.command_report()
        try:
            report.data = preview(TimingSchema().load(timing), datetime.now())
        except ValidationError as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        return report.finish()

    def __retry_retention(self):
        try:
            for task in RetentionService.pending_tasks(agent_settings):
                self.__execution_manager().submit(
                    self.cmd_run_retention, **task, retry=True,
                    resources={("job", task["job_id"]), ("repository", task["repository_id"])},
                )
        except Exception:
            logging.exception("Could not retry pending retention")

    """ Cancel running backup job """

    def cmd_cancel_job(self, job_id, operation_uuid=None, cancel_if_missing=False):
        report = AgentReport.command_report()
        with _COMMAND_ADMISSION_LOCK:
            if cancel_if_missing and operation_uuid and not agent_operations.find_one(uuid=operation_uuid):
                cancelled = AgentReport.backup_operation(job_id=job_id, repository_id=None, operation_uuid=operation_uuid)
                cancelled.log_message("Chain cancelled before job admission", final_state=AgentReportState.cancelled)
                cancelled.finish()
                report.set_data("not_admitted", True)
                report.log_message("Pending chain job cancelled")
                return report.finish()
            if operation_uuid and self.__execution_manager().cancel(operation_uuid):
                job_report = AgentReport.backup_operation(
                    job_id=job_id,
                    repository_id=None,
                    operation_uuid=operation_uuid,
                )
                job_report.log_message(
                    f"Canceled queued job {job_id} by user request",
                    final_state=AgentReportState.cancelled,
                )
                job_report.finish()
                report.log_message(f"Canceled queued job {job_id}")
                return report.finish()

        job_report = (
            AgentReport.get_report(uuid=operation_uuid)
            if operation_uuid
            else AgentReport.get_report(job_id=job_id)
        )

        if job_report:
            job_report.log_message(f"Canceling job {job_id} by user request")

            if cancel_if_missing:
                job_report.cancel_event.set()
                job_report.final_state = AgentReportState.cancelled
                pid = job_report.data.get("pid")
                if pid:
                    self.__resticapi.cancel_process(pid)
                report.log_message("Chain job cancellation requested")
                return report.finish()

            job = jobs.find_one(id=job_id)
            if job and job.get("type") == "truenas":
                job_report.cancel_event.set()
                job_report.final_state = AgentReportState.cancelled
                report.log_message(f"Cancellation requested for TrueNAS job {job_id}")
                return report.finish()

            pid = self.__wait_for_report_pid(job_report)
            if not pid:
                report.log_message(
                    f"Failed to cancel job {job_id}. No restic process became available",
                    final_state=AgentReportState.failed,
                )
                return report.finish()

            # Cancel restic process
            try:
                cancelled = self.__resticapi.cancel_process(pid)
            except Exception as exc:
                logging.exception("Failed to cancel restic process %s", pid)
                cancelled = False
                cancel_reason = str(exc)
            else:
                cancel_reason = f"Restic process {pid} is no longer running"
            if not cancelled:
                report.log_message(
                    f"Failed to cancel job {job_id}. {cancel_reason}",
                    final_state=AgentReportState.failed,
                )
                return report.finish()
            job_report.final_state = AgentReportState.cancelled

            report.log_message(f"Canceled job {job_id}")
        else:
            report.log_message(
                f"Failed to cancel job {job_id}. No running job found",
                final_state=AgentReportState.failed,
            )

        return report.finish()

    """ Run restore point retention """

    def cmd_run_retention(self, repository_id, retention_id, job_id, current_operation_uuid=None, retry=False):
        return RetentionService.run(
            repository_id=repository_id,
            retention_id=retention_id,
            job_id=job_id,
            current_operation_uuid=current_operation_uuid,
            set_repository=self.__set_repository,
            resticapi=self.__resticapi,
            retentions_table=retentions,
            operations_table=agent_operations,
            operation_artifacts_table=agent_operation_artifacts,
            settings_table=agent_settings,
            retry=retry,
        )

    """ Unlock repository """

    def cmd_unlock_repository(self, repository_id, operation_uuid=None):
        report = AgentReport(
            type=AgentReportType.repository_unlock,
            operation_uuid=operation_uuid,
            repository_id=repository_id,
        )

        try:
            self.__set_repository(repository_id)
            unlock_output = self.__resticapi.unlock()
            report.log_message(unlock_output.capitalize() if unlock_output else "No locks")
        except (ResticError, AgentExeption) as e:
            report.log_message(f"{e}", final_state=AgentReportState.failed)

        return report.finish()

    def __restore_context(self):
        return RestoreContext(self.resticapi, self.configure_repository)

    def cmd_list_restore_snapshots(self, repository, tags):
        report = AgentReport.command_report(data={"snapshots": []})
        try:
            report.data["snapshots"] = RestoreService.list_snapshots(
                agent=self.__restore_context(),
                repository=repository,
                tags=tags,
            )
        except (ResticError, AgentExeption) as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        return report.finish()

    def cmd_list_restore_entries(self, repository, snapshot_id, path="/"):
        report = AgentReport.command_report(data={"entries": []})
        try:
            report.data["entries"] = RestoreService.list_entries(
                agent=self.__restore_context(),
                repository=repository,
                snapshot_id=snapshot_id,
                path=path,
            )
        except (ResticError, AgentExeption) as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        return report.finish()

    def cmd_run_restore(self, **kwargs):
        return RestoreService.run_restore(agent=self.__restore_context(), **kwargs)

    def cmd_proxmox_restore(self, action, **kwargs):
        from drastic_agent.services.proxmox_restore import (
            guest_tools_available,
            host_options,
            session_action,
        )

        report = AgentReport.command_report()
        cancelled = getattr(self, "_Agent__shutdown_event", Event()).is_set
        try:
            if action == "options":
                report.data = host_options(cancelled) if kwargs.get("mode", "proxmox_vm") == "proxmox_vm" else {}
                report.data["guest_files_on_demand"] = True
                try:
                    guest_tools_available()
                    report.data["guest_files_error"] = None
                except ValueError as exc:
                    report.data["guest_files_error"] = str(exc)
            elif action == "entries":
                future = self.__execution_manager().submit(
                    session_action, action, agent=self.__restore_context(), cancelled=cancelled,
                    resources={("restore-session", kwargs.get("session_id"))}, **kwargs,
                )
                if future is None:
                    raise ValueError("Execution capacity or restore workspace is busy")
                report.data = future.result()
            else:
                report.data = session_action(action, agent=self.__restore_context(), **kwargs)
        except Exception as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        return report.finish()

    def cmd_cancel_restore(self, operation_uuid=None, report_uuid=None):
        operation_uuid = operation_uuid or report_uuid
        report = AgentReport.command_report()
        with _COMMAND_ADMISSION_LOCK:
            if operation_uuid and self.__execution_manager().cancel(operation_uuid):
                restore_report = AgentReport.restore_operation(
                    operation_uuid=operation_uuid,
                    job_id=None,
                    repository_id=None,
                )
                restore_report.log_message(
                    f"Canceled queued restore {operation_uuid} by user request",
                    final_state=AgentReportState.cancelled,
                )
                restore_report.finish()
                report.log_message(f"Canceled queued restore {operation_uuid}")
                return report.finish()

        restore_report = AgentReport.get_report(uuid=operation_uuid)

        if not restore_report:
            report.log_message(
                f"Failed to cancel restore {operation_uuid}. No running restore found",
                final_state=AgentReportState.failed,
            )
            return report.finish()

        restore_report.log_message(f"Canceling restore {operation_uuid} by user request")
        restore_report.cancel_event.set()
        report.log_message(f"Cancellation requested for restore {operation_uuid}")
        return report.finish()
