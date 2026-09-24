import bz2
import configparser
import hashlib
import json
import logging
import os
import platform
import re
import secrets
import stat
import subprocess
import tempfile
from concurrent.futures import CancelledError
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic, sleep
from urllib.parse import urlparse, urlunparse
from zipfile import ZipFile

import requests
import socketio
from croniter import croniter
from docker import DockerClient
from docker.errors import DockerException
from marshmallow import ValidationError
from socketio.exceptions import ConnectionError
from sqlalchemy.exc import IntegrityError

from drastic_agent.agent.action import AgentAction
from drastic_agent.agent.database import (
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
)
from drastic_agent.agent.enums import AgentReportState, AgentReportType
from drastic_agent.agent.exceptions import AgentExeption
from drastic_agent.agent.execution import ExecutionManager
from drastic_agent.agent.operation_store import operation_store
from drastic_agent.agent.report import AgentReport
from drastic_agent.agent.schemas import AgentReportSchema
from drastic_agent.config import DefaultConfig, env_flag, env_int, env_value
from drastic_agent.jobs.registry import get_job_handler
from drastic_agent.proxmox import ProxmoxApiClient, ProxmoxError, get_proxmox_guest_driver
from drastic_agent.services.restore import RestoreService
from drastic_agent.services.retention import RetentionService
from drastic_agent.version import agent_version
from drastic_common.agent.commands import (
    ASYNC_AGENT_COMMANDS,
    AgentCommandName,
    AgentCommandRequestSchema,
)
from drastic_common.agent.enums import AgentJobActionModule, AgentRepositoryKind
from drastic_common.restic import RESTIC_VERSION, ResticApi
from drastic_common.restic.exceptions import ResticError
from drastic_common.restic.repository import ResticRepository
from drastic_common.secret_envelope import (
    SecretEnvelopeError,
    decrypt_with_private_key,
    encrypt_for_public_key,
    generate_agent_keypair,
)
from drastic_common.ssh_keys import generate_ssh_keypair


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


def _rewrite_managed_restic_location(location: str, server_url: str | None) -> str:
    raw_location = str(location or "").strip()
    effective_server_url = str(server_url or "").strip().rstrip("/")
    if not raw_location.startswith("rest:") or not effective_server_url:
        return raw_location

    parsed = urlparse(raw_location.removeprefix("rest:"))
    if "/restic/" not in parsed.path:
        return raw_location

    server = urlparse(effective_server_url)
    rewritten = parsed._replace(scheme=server.scheme, netloc=server.netloc)
    return f"rest:{urlunparse(rewritten)}"


def _build_managed_restic_location(server_url: str | None, repository_path: str) -> str:
    effective_server_url = str(server_url or "").strip().rstrip("/")
    normalized_path = str(repository_path or "").strip().strip("/")
    if not effective_server_url or not normalized_path:
        return str(repository_path or "")

    return f"rest:{effective_server_url}/restic/{normalized_path}"


def _encode_config_secret(value: str) -> str:
    return value.replace("\n", "\\n")


def _decode_config_secret(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("\\n", "\n")


def _json_mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _redact_secrets(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower() in {
                "password",
                "recovery_key",
                "encrypted_recovery_key",
                "encrypted_agent_key",
                "encrypted_restic_access_key",
                "encrypted_value",
                "agent_secret",
            }:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _is_restic_already_initialized_error(error: Exception) -> bool:
    message = str(error).lower()
    return "already initialized" in message or "config file already exists" in message


def _is_restic_uninitialized_error(error: Exception) -> bool:
    message = str(error).lower()
    return "unable to open config file" in message or "is there a repository at" in message


def _is_restic_auth_error(error: Exception) -> bool:
    message = str(error).lower()
    return "wrong password" in message or "no key found" in message


def _sha256sum_entry(sums_text: str, archive_name: str) -> str | None:
    for line in str(sums_text or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == archive_name:
            return parts[0].lower()
    return None


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _restic_binary_is_valid(binary_path: str) -> bool:
    if not os.path.isfile(binary_path) or not os.access(binary_path, os.X_OK):
        return False

    try:
        result = subprocess.run(
            [binary_path, "version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    output = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and re.search(
        rf"\brestic\s+{re.escape(RESTIC_VERSION)}(?:\s|$)", output
    ) is not None


class Agent:
    identifier = None
    client = None
    __docker_client = None
    __secret = None
    __resticapi = None
    __config = None
    __not_configured_logged = False
    __managed_repo_rewrite_warning_logged = False

    def __init__(self, server=None, identifier=None, secret=None):
        # Initalize agent information
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
        self.__execution = ExecutionManager()

        # Download binary if not exists
        self.__check_restic_binary()

        # Connect to docker.sock if exists
        if os.path.exists("/var/run/docker.sock"):
            try:
                self.__docker_client = DockerClient(base_url="unix://var/run/docker.sock")
            # Do nothing on Exeption
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

    """ Download restic binary from github """

    def __check_restic_binary(self):
        # Generate restic binary name for the recent supported resticapi version and current platform
        binary_name = f"restic_{RESTIC_VERSION}_{self.os_clean}_{self.arch}"

        # Binary folder and path
        binary_folder = _agent_data_path("bin")
        binary_path = os.path.join(binary_folder, binary_name)

        os.makedirs(binary_folder, exist_ok=True)
        if _restic_binary_is_valid(binary_path):
            logging.info(f"Found restic binary at: {binary_path}")
        else:
            if os.path.exists(binary_path):
                logging.warning("Existing restic binary is invalid. Attempting download...")
            else:
                logging.info("Restic binary not found. Attempting download...")
            self.__download_restic_binary(binary_folder, binary_name, binary_path)

        # Create ResticApi instance with binary path
        shutdown_event = getattr(self, "_Agent__shutdown_event", None)
        if shutdown_event is None:
            shutdown_event = Event()
            self.__shutdown_event = shutdown_event
        self.__resticapi = ResticApi(
            binary_path=binary_path,
            cancellation_event=shutdown_event,
            timeout=env_int("DRASTIC_RESTIC_TIMEOUT_SECONDS", DefaultConfig.RESTIC_TIMEOUT_SECONDS),
        )

    def __download_restic_binary(self, binary_folder, binary_name, binary_path):
        archive_name = f"{binary_name}.{'zip' if 'windows' in binary_name else 'bz2'}"
        url = f"https://github.com/restic/restic/releases/download/v{RESTIC_VERSION}/{archive_name}"
        checksums_url = (
            f"https://github.com/restic/restic/releases/download/v{RESTIC_VERSION}/SHA256SUMS"
        )
        archive_path = None
        candidate_path = None

        logging.info(f"Downloading from {url}...")
        try:
            archive_fd, archive_path = tempfile.mkstemp(
                dir=binary_folder, prefix=f".{archive_name}.", suffix=".tmp"
            )
            with os.fdopen(archive_fd, "wb") as archive_file:
                response = requests.get(url, timeout=120)
                response.raise_for_status()
                archive_file.write(response.content)

            checksums_response = requests.get(checksums_url, timeout=120)
            checksums_response.raise_for_status()
            expected_checksum = _sha256sum_entry(checksums_response.text, archive_name)
            if not expected_checksum:
                raise AgentExeption(
                    f"Restic checksum entry for {archive_name} not found in SHA256SUMS"
                )
            actual_checksum = _sha256_file(archive_path)
            if actual_checksum.lower() != expected_checksum:
                raise AgentExeption(
                    "Restic binary checksum mismatch: "
                    f"expected {expected_checksum}, got {actual_checksum}"
                )

            candidate_fd, candidate_path = tempfile.mkstemp(
                dir=binary_folder, prefix=f".{binary_name}.", suffix=".tmp"
            )
            with os.fdopen(candidate_fd, "wb") as target:
                if archive_name.endswith(".bz2"):
                    with bz2.open(archive_path, "rb") as source:
                        target.write(source.read())
                else:
                    with ZipFile(archive_path) as zip_file:
                        members = [member for member in zip_file.filelist if not member.is_dir()]
                        if not members:
                            raise AgentExeption("Restic archive did not contain a binary")
                        with zip_file.open(members[0]) as source:
                            target.write(source.read())

            mode = os.stat(candidate_path).st_mode
            os.chmod(candidate_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if not _restic_binary_is_valid(candidate_path):
                raise AgentExeption(
                    f"Downloaded restic binary failed version check for {RESTIC_VERSION}"
                )

            os.replace(candidate_path, binary_path)
            candidate_path = None
            logging.info(f"Downloaded restic version {RESTIC_VERSION} to {binary_path}")
        except Exception as exc:
            error = f"Restic binary download failed: {exc}"
            logging.error(error)
            raise AgentExeption(error) from exc
        finally:
            for temporary_path in (candidate_path, archive_path):
                if temporary_path:
                    try:
                        os.unlink(temporary_path)
                    except FileNotFoundError:
                        pass

    """ Start job & report scheduler (Blocking) """

    def __start_scheduler(self):
        sleep_time = 0.1

        while not self.__shutdown_event.wait(sleep_time):
            now = datetime.now()

            self.__run_due_schedules(now)

            # Process tasks that require server connection
            if self.connected:
                self.__flush_report_queue()
            else:
                self.__maintain_server_connection(now)


    def __run_due_schedules(self, now):
        utc_slot = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
        planned_slot = utc_slot.isoformat()
        run_slots = getattr(self, "_Agent__schedule_run_slots", {})
        self.__schedule_run_slots = {
            schedule_id: slot for schedule_id, slot in run_slots.items() if slot == planned_slot
        }

        # Process job schedules (Works without server connection as long as job data is synced)
        for job in jobs:
            for schedule in schedules.find(job_id=job["id"]):
                cron_string = schedule.get("cron_string")
                if not cron_string or not croniter.match(cron_string, now):
                    continue

                schedule_id = schedule.get("id")
                if schedule_id is None:
                    logging.warning("Ignoring schedule without id for job %s", job["id"])
                    continue
                if self.__schedule_run_slots.get(schedule_id) == planned_slot:
                    continue

                created_at = datetime.now(timezone.utc).isoformat()
                try:
                    schedule_run_id = schedule_runs.insert(
                        {
                            "schedule_id": schedule_id,
                            "planned_slot": planned_slot,
                            "status": "claimed",
                            "reason": None,
                            "created_at": created_at,
                            "updated_at": created_at,
                        },
                        ensure=False,
                    )
                except IntegrityError:
                    self.__schedule_run_slots[schedule_id] = planned_slot
                    continue
                self.__schedule_run_slots[schedule_id] = planned_slot

                logging.info(f"Starting job {job['id']} by schedule {cron_string}")
                try:
                    future = self.__execution_manager().submit(
                        self.cmd_run_job,
                        job["id"],
                        schedule["repository_id"],
                        schedule.get("retention_id"),
                        run_options={
                            **(schedule.get("config") or {}),
                            "schedule_id": schedule_id,
                        },
                        resources={("job", job["id"]), ("repository", schedule["repository_id"])},
                    )
                except Exception as exc:
                    logging.exception("Could not submit scheduled job %s", job["id"])
                    self.__update_schedule_run(schedule_run_id, "skipped", str(exc))
                    continue

                if future is None:
                    self.__update_schedule_run(
                        schedule_run_id,
                        "skipped",
                        "execution capacity or requested resource is busy",
                    )
                else:
                    self.__update_schedule_run(schedule_run_id, "started")
                    if hasattr(future, "add_done_callback"):
                        future.add_done_callback(
                            lambda completed, run_id=schedule_run_id: self.__finish_schedule_run(
                                run_id, completed
                            )
                        )

    def __finish_schedule_run(self, schedule_run_id, future):
        try:
            report = future.result()
        except CancelledError:
            self.__update_schedule_run(schedule_run_id, "failed", "execution was cancelled")
            return
        except Exception as exc:
            logging.exception("Scheduled operation failed")
            self.__update_schedule_run(schedule_run_id, "failed", str(exc))
            return

        state = getattr(report, "state", None)
        if state in {AgentReportState.failed, AgentReportState.cancelled}:
            reason = getattr(report, "log", None) or "operation reported failure"
            self.__update_schedule_run(schedule_run_id, "failed", reason)
        else:
            self.__update_schedule_run(schedule_run_id, "finished")

    def __update_schedule_run(self, schedule_run_id, status, reason=None):
        schedule_runs.update(
            {
                "id": schedule_run_id,
                "status": status,
                "reason": reason,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ["id"],
            ensure=False,
        )

    def __recover_schedule_runs(self):
        for status in ("claimed", "started"):
            for run in list(schedule_runs.find(status=status)):
                self.__update_schedule_run(
                    run["id"],
                    "failed",
                    "agent stopped before scheduled execution completed",
                )

    def __prune_schedule_runs(self, now=None):
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=90)
        for status in ("finished", "failed", "skipped"):
            for run in list(schedule_runs.find(status=status)):
                try:
                    updated_at = datetime.fromisoformat(str(run["updated_at"]).replace("Z", "+00:00"))
                except (KeyError, TypeError, ValueError):
                    continue
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                if updated_at < cutoff:
                    schedule_runs.delete(id=run["id"])

    def __flush_report_queue(self):
        # Send update for pending job reports
        for report in AgentReport.pending_reports:
            if not report.sent:
                report.sent = self.__send_report(report)

        # A triggered child must not overtake its parent. Blocked children and
        # failed sends rotate so unrelated reports still make progress.
        queue = AgentReport.finished_reports
        outstanding_uuids = {report.uuid for report in queue}
        outstanding_uuids.update(report.uuid for report in AgentReport.pending_reports)
        for _ in range(len(queue)):
            report = queue.popleft()
            if report.parent_operation_uuid in outstanding_uuids:
                queue.append(report)
                continue

            logging.debug(f"Popped report for job {report.uuid} from report queue")
            if self.__send_report(report):
                operation_store.delete(report.uuid)
            else:
                queue.append(report)
            break

    def __maintain_server_connection(self, now):
        if self.connected:
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

        if now < self.__next_reconnect_attempt_at:
            return

        self.__next_reconnect_attempt_at = now + self.__reconnect_retry_interval
        self.__connect()

    """ Send request to server """

    def __send_request(self, __action, **kwargs):
        if self.connected:
            arguments = {}
            for key, value in kwargs.items():
                arguments[key] = value

            try:
                return self.client.call(
                    "request",
                    {"action": __action, "args": arguments},
                    namespace="/agent",
                    timeout=env_int(
                        "DRASTIC_COMMAND_TIMEOUT_SECONDS", DefaultConfig.COMMAND_TIMEOUT_SECONDS
                    ),
                )
            except Exception as exc:
                logging.warning(f"Agent request '{__action}' failed: {exc}")
                try:
                    self.client.disconnect()
                except Exception:
                    pass
                self.client = None

        return {"success": False, "result": {}}

    """ Send report to server """

    def __send_report(self, report):
        try:
            report_json = AgentReportSchema().dump(report)
            logging.debug(f"Sending Report {report.uuid}")
            logging.debug(f"JSON-Data: {report_json}")
            request = self.__send_request("operation", operation_json=report_json)
            success = bool(request.get("success"))
            if success:
                report.mark_logs_sent()
            return success
        except Exception as e:
            logging.debug(f"Failed to send report {report.uuid}: {e}")

        return False

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
        env = dict(repository.get("environment") or {})
        location = repository["location"]
        if repository.get("kind") == AgentRepositoryKind.native.value:
            if str(location).startswith("rest:"):
                location = _rewrite_managed_restic_location(location, self.__server)
            else:
                location = _build_managed_restic_location(self.__server, location)
        elif self.managed_repo_rewrite_enabled:
            location = _rewrite_managed_restic_location(location, self.__server)

        if (
            location.startswith("rest:")
            and "/restic/" in location
            and self.identifier
            and self.__secret
        ):
            env["RESTIC_REST_USERNAME"] = str(self.identifier)
            env["RESTIC_REST_PASSWORD"] = self.__secret

        return location, env

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
        repository_id = repository.get("id")
        cache = self.__repository_password_cache()
        if repository_id in cache:
            return cache[repository_id]

        encrypted_agent_key = _json_mapping(repository.get("encrypted_restic_access_key"))
        if not encrypted_agent_key:
            return None

        private_key = self.private_key
        if not private_key:
            raise AgentExeption(f"Repository {repository_id} cannot be unlocked without agent key")

        try:
            password = decrypt_with_private_key(encrypted_agent_key, private_key)
        except SecretEnvelopeError as exc:
            raise AgentExeption(f"Repository {repository_id} agent key envelope is invalid") from exc

        cache[repository_id] = password
        return password

    def __repository_recovery_password(self, repository):
        repository_id = repository.get("id")
        password_secret_id = repository.get("password_secret_id")
        if password_secret_id in self.__secret_value_cache():
            return self.__secret_value_cache()[password_secret_id]["value"]

        encrypted_recovery_key = self.__repository_recovery_envelope(repository)
        if not encrypted_recovery_key:
            raise AgentExeption(
                f"Repository {repository_id} has no agent key and no recovery envelope"
            )

        private_key = self.private_key
        if not private_key:
            raise AgentExeption(f"Repository {repository_id} cannot be provisioned without agent key")

        try:
            return decrypt_with_private_key(encrypted_recovery_key, private_key)
        except SecretEnvelopeError as exc:
            raise AgentExeption(f"Repository {repository_id} provisioning envelope is invalid") from exc

    def __repository_recovery_envelope(self, repository):
        encrypted_recovery_key = _json_mapping(repository.get("encrypted_recovery_key"))
        if encrypted_recovery_key:
            return encrypted_recovery_key

        request = self.__send_request(
            "repository_recovery_envelope",
            repository_id=repository.get("id"),
        )
        if request.get("success"):
            return _json_mapping((request.get("result") or {}).get("encrypted_recovery_key"))

        return None

    def __strip_repository_secret_fields(self, repository):
        sanitized = dict(repository)
        sanitized.pop("encrypted_recovery_key", None)
        return sanitized

    def __store_repository_agent_key(self, repository_id, password):
        public_key = self.public_key
        if not public_key:
            raise AgentExeption(f"Repository {repository_id} agent key cannot be sealed without public key")

        try:
            encrypted_agent_key = encrypt_for_public_key(password, public_key)
        except SecretEnvelopeError as exc:
            raise AgentExeption(f"Repository {repository_id} agent key could not be sealed") from exc

        repositories.update(
            {"id": repository_id, "encrypted_restic_access_key": encrypted_agent_key},
            ["id"],
            types={"encrypted_restic_access_key": db.types.json},
        )

        request = self.__send_request(
            "store_repository_agent_key",
            repository_id=repository_id,
            encrypted_agent_key=encrypted_agent_key,
        )
        if not request.get("success"):
            raise AgentExeption(f"Repository {repository_id} agent key could not be stored on backend")

    def __clear_local_repository_secret(self, repository_id):
        try:
            repository_secrets.delete(repository_id=repository_id)
        except Exception:
            pass

    def __provision_agent_repository_password(self, repository, location, env, initialize):
        repository_id = repository.get("id")
        agent_password = self.__repository_password_cache().get(repository_id) or secrets.token_urlsafe(32)
        recovery_password = self.__repository_recovery_password(repository)

        self.__resticapi.set_repository(
            ResticRepository(
                location=location,
                password=recovery_password,
                env=env,
                ssh_private_key=self.__repository_ssh_private_key(repository),
                ssh_known_hosts_path=self.__ssh_known_hosts_path(),
            )
        )
        if initialize:
            try:
                self.__resticapi.init()
            except ResticError as exc:
                if not _is_restic_already_initialized_error(exc):
                    raise AgentExeption(
                        f"Repository {repository_id} initialization failed: {exc}"
                    ) from exc
        else:
            try:
                self.__resticapi.cat_config()
            except ResticError as exc:
                raise AgentExeption(
                    f"Repository {repository_id} recovery password failed: {exc}"
                ) from exc

        try:
            self.__resticapi.key_add(agent_password)
        except ResticError as exc:
            raise AgentExeption(f"Repository {repository_id} agent key provisioning failed: {exc}") from exc

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
        client = socketio.Client(logger=False, engineio_logger=False, reconnection=False)

        @client.event(namespace="/agent")
        def connect():
            logging.info("Connected to server")

        @client.event(namespace="/agent")
        def disconnect():
            logging.warning("Disconnected from server")

        @client.event(namespace="/agent")
        def connect_error(data=None):
            logging.warning(f"Agent connection failed: {data}")

        @client.on("execute", namespace="/agent")
        def on_execute(data=None):
            return AgentReportSchema().dump(self.__handle_execute_command(data or {}))

        return client

    def __run_command_async(self, command_name, command_args):
        prefixed_command = f"cmd_{command_name}"
        operation_uuid = command_args.get("operation_uuid") or command_args.get("report_uuid")
        reservation = None
        reservation_created = False
        if operation_uuid:
            reservation, reservation_created = AgentReport.reserve_history_operation(
                type=self.__operation_type_for_command(command_name),
                operation_uuid=operation_uuid,
                job_id=command_args.get("job_id"),
                repository_id=command_args.get("repository_id"),
                retention_id=command_args.get("retention_id"),
            )

        def runner():
            try:
                result = getattr(self, prefixed_command)(**command_args)
                if (
                    operation_uuid
                    and getattr(result, "type", None) == AgentReportType.command
                    and getattr(result, "final_state", None) == AgentReportState.failed
                ):
                    self.__finish_unexpected_async_failure(
                        command_name,
                        command_args,
                        getattr(result, "log", None) or "command returned failure",
                    )
            except Exception as exc:
                logging.exception(
                    f"Async agent command {command_name} failed with arguments: {command_args}"
                )
                if operation_uuid:
                    self.__finish_unexpected_async_failure(command_name, command_args, exc)

        resources = set()
        if command_args.get("job_id") is not None:
            resources.add(("job", command_args["job_id"]))
        if command_args.get("repository_id") is not None:
            resources.add(("repository", command_args["repository_id"]))
        future = self.__execution_manager().submit(
            runner,
            resources=resources,
            operation_uuid=operation_uuid,
        )
        if future is None and reservation_created:
            AgentReport.discard_history_reservation(reservation)
        return future

    @staticmethod
    def __operation_type_for_command(command_name):
        return {
            AgentCommandName.run_job.value: AgentReportType.backup,
            AgentCommandName.run_restore.value: AgentReportType.restore,
            AgentCommandName.check_repository.value: AgentReportType.repository_check,
            AgentCommandName.unlock_repository.value: AgentReportType.repository_unlock,
        }[command_name]

    def __operation_report_for_command(self, command_name, command_args):
        operation_uuid = command_args.get("operation_uuid") or command_args.get("report_uuid")
        if command_name == AgentCommandName.run_job.value:
            return AgentReport.backup_operation(
                job_id=command_args.get("job_id"),
                repository_id=command_args.get("repository_id"),
                retention_id=command_args.get("retention_id"),
                operation_uuid=operation_uuid,
            )
        if command_name == AgentCommandName.run_restore.value:
            return AgentReport.restore_operation(
                operation_uuid=operation_uuid,
                job_id=command_args.get("job_id"),
                repository_id=command_args.get("repository_id"),
            )
        if command_name == AgentCommandName.check_repository.value:
            return AgentReport.check_operation(
                repository_id=command_args.get("repository_id"),
                operation_uuid=operation_uuid,
            )
        return AgentReport(
            type=AgentReportType.repository_unlock,
            repository_id=command_args.get("repository_id"),
            operation_uuid=operation_uuid,
        )

    def __finish_unexpected_async_failure(self, command_name, command_args, exc):
        operation_uuid = command_args.get("operation_uuid") or command_args.get("report_uuid")
        if any(report.uuid == operation_uuid for report in AgentReport.finished_reports):
            return
        history = agent_operations.find_one(uuid=operation_uuid)
        if history and history.get("state") != AgentReportState.running.name:
            return

        report = AgentReport.get_report(uuid=operation_uuid)
        if report is None:
            report = self.__operation_report_for_command(command_name, command_args)
        report.log_message(
            f"Unexpected worker failure while executing {command_name}: {exc}",
            final_state=AgentReportState.failed,
        )
        report.finish()

    def __validate_run_job_admission(self, command_args):
        job_id = command_args.get("job_id")
        repository_id = command_args.get("repository_id")
        try:
            job = jobs.find_one(id=job_id)
            repository = repositories.find_one(id=repository_id)
        except Exception as exc:
            return f"Local synced job data could not be validated: {exc}"
        if not job:
            return f"Job with id {job_id} is not available in local sync data"
        if not repository:
            return f"Repository with id {repository_id} is not available in local sync data"
        return None

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
        command_name = command.value
        command_args = dict(command_request.get("args") or {})
        logging.info(
            f"Executing command {command_name} with arguments: {_redact_secrets(command_args)}"
        )
        prefixed_command = f"cmd_{command_name}"

        if not hasattr(self, prefixed_command):
            report = AgentReport.command_report()
            report.log_message(
                f"Command {command_name} not supported",
                final_state=AgentReportState.failed,
            )
            return report.finish()

        if command in ASYNC_AGENT_COMMANDS:
            operation_uuid = command_args.get("operation_uuid") or command_args.get("report_uuid")
            if not operation_uuid:
                report = AgentReport.command_report()
                report.log_message(
                    f"Could not start {command_name}: operation_uuid is required",
                    final_state=AgentReportState.failed,
                )
                return report.finish()
            if command == AgentCommandName.run_job:
                rejection_reason = self.__validate_run_job_admission(command_args)
                if rejection_reason:
                    report = AgentReport.command_report()
                    report.log_message(
                        f"Could not start run_job: {rejection_reason}",
                        final_state=AgentReportState.failed,
                    )
                    return report.finish()
            future = self.__run_command_async(command_name, command_args)
            report = AgentReport.command_report()
            if future is None:
                report.log_message(
                    f"Could not start {command_name}: execution capacity or requested resource is busy",
                    final_state=AgentReportState.failed,
                )
                return report.finish()
            if command == AgentCommandName.run_job:
                report.log_message(
                    f"Started job {command_args.get('job_id')} on repository {command_args.get('repository_id')}"
                )
            elif command == AgentCommandName.run_restore:
                report.log_message(f"Started restore operation {command_args.get('operation_uuid')}")
            elif command == AgentCommandName.unlock_repository:
                report.log_message(
                    f"Started repository unlock on repository {command_args.get('repository_id')}"
                )
            else:
                report.log_message(
                    f"Started repository check on repository {command_args.get('repository_id')}"
                )
            return report.finish()

        return getattr(self, prefixed_command)(**command_args)

    def __connect(self):
        if not self.configured:
            if not self.__not_configured_logged:
                logging.info(
                    "Agent is not configured yet. Waiting for registration credentials or a saved agent config."
                )
                self.__not_configured_logged = True
            return False

        if self.connected:
            return True

        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client = None

        client = self.__create_client()

        try:
            client.connect(self.__server, auth=self.authdata, namespaces=["/agent"])
            self.client = client
            self.cmd_sync()
            return True
        except ConnectionError as exc:
            logging.warning(f"Could not connect to server {self.__server}: {exc}")
        except Exception as exc:
            logging.warning(f"Unexpected agent connection error: {exc}")

        try:
            client.disconnect()
        except Exception:
            pass

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

        if "AGENT" in self.__config and not self.__config.get("AGENT", "private_key", fallback=None):
            private_key, public_key = generate_agent_keypair()
            self.__config["AGENT"]["private_key"] = _encode_config_secret(private_key)
            self.__config["AGENT"]["public_key"] = _encode_config_secret(public_key)
            self.save_config()

        if self.configured:
            self.__not_configured_logged = False

        # Save configuration
        # self.save_config()

        # self.__load_sshkey()

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

        AgentReport.cancel_running()
        if hasattr(self, "_Agent__shutdown_event"):
            self.__shutdown_event.set()

        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass

        if hasattr(self, "_Agent__execution"):
            self.__execution.shutdown(wait=True)

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

    # def __load_sshkey(self):

    #     ssh_pubkey = os.path.join( os.environ['HOME'], '.ssh', 'id_rsa.pub' )

    #     if os.path.exists(ssh_pubkey):
    #         with open(ssh_pubkey, 'r') as file:
    #             self.ssh_pubkey = file.read()

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
            "agent_install_type": self.install_type,
            "agent_public_key": self.public_key,
            "agent_ssh_public_key": self.ssh_public_key,
        }

    @property
    def private_key(self):
        if "AGENT" not in self.__config:
            return None
        return _decode_config_secret(self.__config.get("AGENT", "private_key", fallback=None))

    @property
    def public_key(self):
        if "AGENT" not in self.__config:
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

    def cmd_get_proxmox_guests(self):
        report = AgentReport.command_report(data={"guests": []})

        try:
            api = ProxmoxApiClient()
            driver = get_proxmox_guest_driver()
            report.data["guests"] = driver.list_supported_guests(api=api)
        except ProxmoxError as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)

        return report.finish()

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
            synced_repositories = [
                self.__strip_repository_secret_fields(repository)
                for repository in server_data["repositories"]
            ]

            with db:
                repository_secrets.delete()

                repositories.delete()
                repositories.insert_many(
                    synced_repositories,
                    types={
                        "environment": db.types.json,
                        "encrypted_restic_access_key": db.types.json,
                    },
                )

                retentions.delete()
                retentions.insert_many(server_data["retentions"])

                jobs.delete()
                jobs.insert_many(server_data["jobs"], types={"config": db.types.json})

                schedules.delete()
                schedules.insert_many(server_data["schedules"], types={"config": db.types.json})

                actions.delete()
                actions.insert_many(server_data["actions"])
        except Exception as exc:
            logging.exception("Agent sync transaction failed")
            report.log_message(f"Agent sync failed: {exc}", final_state=AgentReportState.failed)
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

        return report.finish()

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
        secret_id = envelope.get("user_secret_id")
        encrypted_value = _json_mapping(envelope.get("encrypted_value"))
        if not secret_id or not encrypted_value:
            return

        private_key = self.private_key
        if not private_key:
            raise AgentExeption(f"Secret {secret_id} cannot be unlocked without agent key")

        try:
            value = decrypt_with_private_key(encrypted_value, private_key)
        except SecretEnvelopeError as exc:
            raise AgentExeption(f"Secret {secret_id} envelope is invalid") from exc

        self.__secret_value_cache()[secret_id] = {
            "type": envelope.get("type"),
            "value": value,
            "public_data": envelope.get("public_data") or {},
        }

    """ Run backup job: returns None """

    def cmd_run_job(
        self,
        job_id,
        repository_id,
        retention_id=None,
        operation_uuid=None,
        run_options=None,
    ):
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
                agent=self,
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

    """ Cancel running backup job """

    def cmd_cancel_job(self, job_id, operation_uuid=None):
        report = AgentReport.command_report()
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

    def cmd_run_retention(self, repository_id, retention_id, job_id, current_operation_uuid=None):
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

    def cmd_list_restore_snapshots(self, repository, tags):
        report = AgentReport.command_report(data={"snapshots": []})
        try:
            report.data["snapshots"] = RestoreService.list_snapshots(
                agent=self,
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
                agent=self,
                repository=repository,
                snapshot_id=snapshot_id,
                path=path,
            )
        except (ResticError, AgentExeption) as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        return report.finish()

    def cmd_run_restore(self, **kwargs):
        return RestoreService.run_restore(agent=self, **kwargs)

    def cmd_cancel_restore(self, operation_uuid=None, report_uuid=None):
        operation_uuid = operation_uuid or report_uuid
        report = AgentReport.command_report()
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
        pid = self.__wait_for_report_pid(restore_report)
        if not pid:
            report.log_message(
                f"Failed to cancel restore {operation_uuid}. No restic process became available",
                final_state=AgentReportState.failed,
            )
            return report.finish()

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
                f"Failed to cancel restore {operation_uuid}. {cancel_reason}",
                final_state=AgentReportState.failed,
            )
            return report.finish()
        restore_report.final_state = AgentReportState.cancelled
        report.log_message(f"Canceled restore {operation_uuid}")
        return report.finish()
