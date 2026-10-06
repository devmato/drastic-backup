from drastic_common.agent.commands import AgentCommandName
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationArtifact,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.models.job import Job
from drastic_server.models.repository import Repository
from drastic_server.schemas.repository import RepositorySchema
from drastic_server.services.agent import (
    AgentService,
    is_agent_conflict_response,
    is_agent_timeout_response,
)
from drastic_server.services.agent.operation_start import (
    agent_operation_start_response,
    fail_started_agent_operation,
    start_agent_operation,
    unknown_agent_operation_dispatch_response,
)
from drastic_server.services.exceptions import RestoreServiceException
from drastic_server.services.repository import agent_repository_assignment

RESTORE_MODE_PLAIN_FILE = "plain_file"


class RestoreService:
    @staticmethod
    def job_tag(job):
        return f"job_uuid:{job.uuid}"

    @staticmethod
    def modes_for_job(job):
        modes = [
            {
                "value": RESTORE_MODE_PLAIN_FILE,
                "label": "Plain file restore",
                "description": "Restore selected files or directories to a local path on the selected agent.",
            }
        ]
        if getattr(job.type, "name", None) == "proxmox":
            modes = [
                {"value": "proxmox_vm", "label": "Whole VM", "description": "Restore to a free VMID; the VM stays stopped."},
                {"value": "proxmox_files", "label": "Files from VM", "description": "Export guest files to an agent directory."},
                *modes,
            ]
        return modes

    @staticmethod
    def _get_job(user_id, job_id):
        return Job.query.join(Agent).filter(Job.id == job_id, Agent.user_id == user_id).first_or_404()

    @staticmethod
    def _get_agent(user_id, agent_id):
        return Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id).first_or_404()

    @staticmethod
    def _get_repository(user_id, repository_id):
        return Repository.query.filter(
            Repository.id == repository_id,
            Repository.user_id == user_id,
        ).first_or_404()

    @staticmethod
    def _repository_payload(repository, agent=None):
        payload = RepositorySchema().dump(repository)
        if agent is None:
            return payload

        assignment = agent_repository_assignment(agent, repository.id)
        if assignment and assignment.get("encrypted_restic_access_key"):
            payload["encrypted_restic_access_key"] = assignment["encrypted_restic_access_key"]
        return payload

    @staticmethod
    def _ensure_agent_repository(agent, repository):
        if not any(assigned.id == repository.id for assigned in agent.repositories):
            raise RestoreServiceException("Repository is not assigned to this agent")

    @classmethod
    def _ensure_snapshot_belongs_to_job(cls, agent, repository, snapshot_id, job):
        job_tag = cls.job_tag(job)
        response = AgentService.list_restore_snapshots(
            agent,
            repository=cls._repository_payload(repository, agent),
            tags=[job_tag],
        )
        cls._raise_for_agent_failure(response)
        snapshots = response.get("data", {}).get("snapshots", [])
        snapshot = next((snapshot for snapshot in snapshots if
            snapshot.get("id") == snapshot_id and job_tag in (snapshot.get("tags") or [])
        ), None)
        if snapshot is None:
            raise RestoreServiceException("Snapshot does not belong to this job")
        return snapshot

    @staticmethod
    def _annotate_proxmox_snapshots(job, repository, snapshots):
        artifacts = AgentOperationArtifact.query.join(AgentOperation).filter(
            AgentOperation.job_id == job.id,
            AgentOperation.repository_id == repository.id,
            AgentOperationArtifact.snapshot_id.in_([s.get("id") for s in snapshots]),
        ).all()
        by_snapshot = {artifact.snapshot_id: artifact for artifact in artifacts}
        for snapshot in snapshots:
            artifact = by_snapshot.get(snapshot.get("id"))
            tags = snapshot.get("tags") or []
            snapshot["proxmox_archive"] = ({"source:proxmox", "guest_type:qemu"}.issubset(tags)
                and any(tag in tags for tag in ("backup_method:vzdump", "backup_method:snapshot", "backup_method:native"))
                and "kind:manifest" not in tags)
            if artifact:
                snapshot["guest_name"] = (artifact.data or {}).get("guest_name")
                if artifact.state not in ("success", "warning") or artifact.forgotten_at:
                    snapshot["restore_error"] = "Backup archive did not complete successfully or was forgotten"
        return snapshots

    @classmethod
    def get_options(cls, user_id, job_id):
        job = cls._get_job(user_id, job_id)
        return {
            "job_id": job.id,
            "job_uuid": job.uuid,
            "modes": cls.modes_for_job(job),
        }

    @classmethod
    def list_snapshots(cls, user_id, job_id, agent_id, repository_id):
        job = cls._get_job(user_id, job_id)
        agent = cls._get_agent(user_id, agent_id)
        repository = cls._get_repository(user_id, repository_id)
        cls._ensure_agent_repository(agent, repository)

        if not agent.online:
            raise RestoreServiceException("Agent is offline")

        response = AgentService.list_restore_snapshots(
            agent,
            repository=cls._repository_payload(repository, agent),
            tags=[cls.job_tag(job)],
        )
        cls._raise_for_agent_failure(response)
        snapshots = response.get("data", {}).get("snapshots", [])
        if getattr(job.type, "name", None) == "proxmox":
            snapshots = cls._annotate_proxmox_snapshots(job, repository, snapshots)
        return {"snapshots": snapshots}

    @classmethod
    def proxmox_action(cls, user_id, data):
        agent = cls._get_agent(user_id, data["agent_id"])
        kwargs = {}
        if data["action"] == "options" and (agent.protocol_version or 0) >= 10:
            kwargs["mode"] = data["mode"]
        if data["action"] != "options":
            job = cls._get_job(user_id, data["job_id"])
            repository = cls._get_repository(user_id, data["repository_id"])
            cls._ensure_agent_repository(agent, repository)
            kwargs = {
                "session_id": data["session_id"],
                "identity": {"job_uuid": job.uuid, "repository_id": repository.id, "snapshot_id": data["snapshot_id"]},
                "volume": data.get("volume"), "path": data["path"],
            }
            if data["action"] == "entries" and (agent.protocol_version or 0) >= 10:
                kwargs["repository"] = cls._repository_payload(repository, agent)
        response = AgentService.send_command(agent, AgentCommandName.proxmox_restore,
                                             action=data["action"], timeout=120, **kwargs)
        cls._raise_for_agent_failure(response)
        return response.get("data") or {}

    @classmethod
    def list_entries(cls, user_id, agent_id, repository_id, snapshot_id, path):
        agent = cls._get_agent(user_id, agent_id)
        repository = cls._get_repository(user_id, repository_id)
        cls._ensure_agent_repository(agent, repository)

        if not agent.online:
            raise RestoreServiceException("Agent is offline")

        response = AgentService.list_restore_entries(
            agent,
            repository=cls._repository_payload(repository, agent),
            snapshot_id=snapshot_id,
            path=path,
        )
        cls._raise_for_agent_failure(response)
        return {"entries": response.get("data", {}).get("entries", [])}

    @classmethod
    def start_restore(cls, user_id, data):
        job = cls._get_job(user_id, data["job_id"])
        agent = cls._get_agent(user_id, data["agent_id"])
        repository = cls._get_repository(user_id, data["repository_id"])
        cls._ensure_agent_repository(agent, repository)

        if not agent.online:
            raise RestoreServiceException("Agent is offline")

        allowed = {mode["value"] for mode in cls.modes_for_job(job)}
        if "proxmox_files" in allowed:
            allowed.add("proxmox_prepare")
        if data["mode"] not in allowed:
            raise RestoreServiceException("Unsupported restore mode")

        proxmox = data["mode"] != RESTORE_MODE_PLAIN_FILE
        if proxmox and (agent.protocol_version or 0) < 2:
            raise RestoreServiceException("Update the agent to use Proxmox restore")
        snapshot = cls._ensure_snapshot_belongs_to_job(agent, repository, data["snapshot_id"], job)
        if proxmox:
            if "backup_method:native" in (snapshot.get("tags") or []) and (agent.protocol_version or 0) < 8:
                raise RestoreServiceException("Update the agent to restore native Proxmox backups (protocol 8)")
            if "backup_method:snapshot" in (snapshot.get("tags") or []) and (agent.protocol_version or 0) < 7:
                raise RestoreServiceException("Update the agent to restore Proxmox disk snapshots (protocol 7)")
            cls._annotate_proxmox_snapshots(job, repository, [snapshot])
            if not snapshot["proxmox_archive"] or snapshot.get("restore_error"):
                raise RestoreServiceException(snapshot.get("restore_error") or "Select a Proxmox QEMU archive snapshot")

        keys = ("mode", "snapshot_id", "restore_location", "include_paths", "overwrite_policy")
        if proxmox:
            keys += ("vmid", "storage", "unique", "session_id", "volume")
        parameters = {key: data[key] for key in keys if key in data}

        operation = start_agent_operation(
            agent=agent,
            job=job,
            repository=repository,
            operation_type=AgentOperationType.restore,
            source=AgentOperationSource.manual,
            msg="Restore started",
            log_message="Restore queued",
            data={**parameters, "job_uuid": job.uuid},
        )

        response = AgentService.run_restore(
            agent,
            operation_uuid=operation.uuid,
            job_id=job.id,
            job_uuid=job.uuid,
            repository_id=repository.id,
            repository=cls._repository_payload(repository, agent),
            expected_job_tag=cls.job_tag(job),
            **parameters,
        )
        if is_agent_timeout_response(response):
            return unknown_agent_operation_dispatch_response(
                operation,
                "Restore dispatch status unknown",
            )
        if response.get("state") != AgentOperationState.success:
            message = response.get("log", "Could not start restore")
            fail_started_agent_operation(operation, message)
            exc = RestoreServiceException(message)
            exc.conflict = is_agent_conflict_response(response)
            raise exc

        return agent_operation_start_response(operation, "Restore started")

    @classmethod
    def cancel_restore(cls, user_id, operation_id):
        operation = (
            AgentOperation.query.join(Agent)
            .filter(AgentOperation.id == operation_id, Agent.user_id == user_id)
            .first_or_404()
        )
        if operation.type != AgentOperationType.restore:
            raise RestoreServiceException("Operation is not a restore operation")

        response = AgentService.cancel_restore(operation.agent, operation_uuid=operation.uuid)
        cls._raise_for_agent_failure(response)
        return {"msg": response.get("log") or "Restore cancellation requested"}

    @staticmethod
    def _raise_for_agent_failure(response):
        if response.get("state") == AgentOperationState.success:
            return
        message = response.get("log") or "Agent command failed"
        if is_agent_timeout_response(response):
            raise TimeoutError(message)
        raise RestoreServiceException(message)
