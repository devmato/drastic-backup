"""Restore from verified repository snapshots, optionally filtered by a live job."""

from sqlalchemy.orm import joinedload

from drastic_common.agent.commands import (
    RESTORE_METADATA_OPTIONS_PROTOCOL,
    RESTORE_POINTS_PROTOCOL,
    AgentCommandName,
)
from drastic_common.restic.snapshots import restore_point_key, snapshot_uuid
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
from drastic_server.services.exceptions import RestoreServiceException
from drastic_server.services.operations.lifecycle import (
    agent_operation_start_response,
    fail_started_agent_operation,
    start_agent_operation,
    unknown_agent_operation_dispatch_response,
)
from drastic_server.services.queries import require_result
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
        return require_result(Job.query.join(Agent).filter(Job.id == job_id, Agent.user_id == user_id))

    @staticmethod
    def _get_agent(user_id, agent_id):
        return require_result(Agent.query.filter(Agent.id == agent_id, Agent.user_id == user_id))

    @staticmethod
    def _get_repository(user_id, repository_id):
        return require_result(Repository.query.filter(
            Repository.id == repository_id,
            Repository.user_id == user_id,
        ))

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
        job_tag = cls.job_tag(job) if job else None
        response = AgentService.list_restore_snapshots(
            agent,
            repository=cls._repository_payload(repository, agent),
            tags=[job_tag] if job_tag else [],
        )
        cls._raise_for_agent_failure(response)
        snapshots = response.get("data", {}).get("snapshots", [])
        snapshot = next((snapshot for snapshot in snapshots if
            snapshot.get("id") == snapshot_id
            and (job_tag is None or job_tag in (snapshot.get("tags") or []))
        ), None)
        if snapshot is None:
            raise RestoreServiceException("Snapshot does not belong to this job")
        return snapshot

    @staticmethod
    def snapshot_job_uuid(snapshot):
        return snapshot_uuid(snapshot, "job_uuid")

    @staticmethod
    def _annotate_proxmox_snapshots(repository, snapshots):
        artifacts = AgentOperationArtifact.query.join(AgentOperation).filter(
            AgentOperation.repository_id == repository.id,
            AgentOperationArtifact.snapshot_id.in_([s.get("id") for s in snapshots]),
        ).options(joinedload(AgentOperationArtifact.operation).joinedload(AgentOperation.job)).all()
        by_snapshot = {artifact.snapshot_id: artifact for artifact in artifacts}
        for snapshot in snapshots:
            artifact = by_snapshot.get(snapshot.get("id"))
            tags = snapshot.get("tags") or []
            snapshot["restore_point_id"] = restore_point_key(snapshot)
            snapshot["job_name"] = artifact.operation.job.name if artifact and artifact.operation.job else None
            snapshot["proxmox_archive"] = (bool(snapshot_uuid(snapshot, "job_uuid")) and {"source:proxmox", "guest_type:qemu"}.issubset(tags)
                and any(tag in tags for tag in ("backup_method:snapshot", "backup_method:native"))
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
        job = cls._get_job(user_id, job_id) if job_id is not None else None
        agent = cls._get_agent(user_id, agent_id)
        repository = cls._get_repository(user_id, repository_id)
        cls._ensure_agent_repository(agent, repository)

        if not agent.online:
            raise RestoreServiceException("Agent is offline")

        response = AgentService.list_restore_snapshots(
            agent,
            repository=cls._repository_payload(repository, agent),
            tags=[cls.job_tag(job)] if job else [],
        )
        cls._raise_for_agent_failure(response)
        snapshots = response.get("data", {}).get("snapshots", [])
        snapshots = cls._annotate_proxmox_snapshots(repository, snapshots)
        return {"snapshots": snapshots}

    @classmethod
    def delete_point(cls, user_id, data):
        agent = cls._get_agent(user_id, data["agent_id"])
        repository = cls._get_repository(user_id, data["repository_id"])
        cls._ensure_agent_repository(agent, repository)
        if not agent.online or (agent.protocol_version or 0) < RESTORE_POINTS_PROTOCOL:
            raise RestoreServiceException("An online agent with protocol 22 is required to delete restore points")
        snapshots = cls.list_snapshots(user_id, None, agent.id, repository.id)["snapshots"]
        snapshot = next((item for item in snapshots if item["id"] == data["snapshot_id"]), None)
        if snapshot is None:
            raise RestoreServiceException("Snapshot no longer exists; reload the list")
        key = restore_point_key(snapshot)
        ids = [item["id"] for item in snapshots if restore_point_key(item) == key]
        # The confirmation names this exact set; don't silently delete newly appearing members.
        if set(ids) != set(data["snapshot_ids"]):
            raise RestoreServiceException("Restore point changed; reload the list before deleting")
        # Listing spans remote I/O. Check current rows afterwards, in report-ingestion
        # lock order, so a running report committed during listing is visible here.
        agent = require_result(Agent.query.filter_by(id=agent.id, user_id=user_id).populate_existing().with_for_update())
        if snapshot_uuid(snapshot, "drastic-owner") and (agent.protocol_version or 0) < 23:
            raise RestoreServiceException("Update the agent to delete owner-tagged restore points (protocol 23)")
        repository = require_result(Repository.query.join(Repository.agents).filter(
            Repository.id == repository.id, Repository.user_id == user_id, Agent.id == agent.id
        ).populate_existing().with_for_update())
        operation_uuid = snapshot_uuid(snapshot, "operation_uuid")
        if operation_uuid and AgentOperation.query.filter_by(uuid=operation_uuid, user_id=user_id,
                repository_id=repository.id, state=AgentOperationState.running).populate_existing().with_for_update().first():
            raise RestoreServiceException("This backup is still running")
        operation = start_agent_operation(agent=agent, repository=repository, operation_type=AgentOperationType.retention,
            msg="Restore point deletion started", data={"scope": "repository", "manual_delete": True})
        response = AgentService.send_command(agent, AgentCommandName.delete_restore_point, timeout=5,
            repository_id=repository.id, snapshot_ids=ids, point_key=key, operation_uuid=operation.uuid)
        if is_agent_timeout_response(response):
            return unknown_agent_operation_dispatch_response(operation, "Restore point deletion dispatch status unknown")
        if response.get("state") != AgentOperationState.success:
            message = response.get("log", "Could not start restore point deletion")
            fail_started_agent_operation(operation, message)
            exc = RestoreServiceException(message)
            exc.conflict = is_agent_conflict_response(response)
            raise exc
        return agent_operation_start_response(operation, "Restore point deletion started")

    @classmethod
    def proxmox_action(cls, user_id, data):
        agent = cls._get_agent(user_id, data["agent_id"])
        kwargs = {}
        if data["action"] == "options" and (agent.protocol_version or 0) >= 10:
            kwargs["mode"] = data["mode"]
        if data["action"] != "options":
            job = cls._get_job(user_id, data["job_id"]) if data.get("job_id") is not None else None
            repository = cls._get_repository(user_id, data["repository_id"])
            cls._ensure_agent_repository(agent, repository)
            # The agent binds the session to this identity. Closing a session must
            # still work if retention has since removed its source snapshot.
            job_uuid = job.uuid if job else str(data["job_uuid"])
            kwargs = {
                "session_id": data["session_id"],
                "identity": {"job_uuid": job_uuid, "repository_id": repository.id, "snapshot_id": data["snapshot_id"]},
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
        job = cls._get_job(user_id, data["job_id"]) if data.get("job_id") is not None else None
        agent = cls._get_agent(user_id, data["agent_id"])
        repository = cls._get_repository(user_id, data["repository_id"])
        cls._ensure_agent_repository(agent, repository)

        if not agent.online:
            raise RestoreServiceException("Agent is offline")

        snapshot = cls._ensure_snapshot_belongs_to_job(agent, repository, data["snapshot_id"], job)
        job_uuid = cls.snapshot_job_uuid(snapshot)
        if job_uuid is None and (agent.protocol_version or 0) < RESTORE_POINTS_PROTOCOL:
            raise RestoreServiceException("Update the agent to restore snapshots without Drastic job tags (protocol 22)")
        allowed = {RESTORE_MODE_PLAIN_FILE}
        if job_uuid and "source:proxmox" in (snapshot.get("tags") or []):
            allowed.update(("proxmox_vm", "proxmox_files", "proxmox_prepare"))
        if data["mode"] not in allowed:
            raise RestoreServiceException("Unsupported restore mode")

        if data.get("ownership_by_name") or data.get("skip_xattrs"):
            if data["mode"] != RESTORE_MODE_PLAIN_FILE:
                raise RestoreServiceException("Restore metadata options are only supported for plain file restores")
            if (agent.protocol_version or 0) < RESTORE_METADATA_OPTIONS_PROTOCOL:
                raise RestoreServiceException("Update the agent to use restore metadata options (protocol 17)")

        proxmox = data["mode"] != RESTORE_MODE_PLAIN_FILE
        if proxmox:
            cls._annotate_proxmox_snapshots(repository, [snapshot])
            if not snapshot["proxmox_archive"] or snapshot.get("restore_error"):
                raise RestoreServiceException(snapshot.get("restore_error") or
                    "Proxmox VM/file restore supports only TAR and Native/CBT snapshots; legacy VMA backups are not supported")
            if "backup_method:native" in (snapshot.get("tags") or []) and (agent.protocol_version or 0) < 8:
                raise RestoreServiceException("Update the agent to restore native Proxmox backups (protocol 8)")
            if "backup_method:snapshot" in (snapshot.get("tags") or []) and (agent.protocol_version or 0) < 7:
                raise RestoreServiceException("Update the agent to restore Proxmox disk snapshots (protocol 7)")

        keys = ("mode", "snapshot_id", "restore_location", "include_paths", "overwrite_policy")
        if proxmox:
            keys += ("vmid", "storage", "unique", "session_id", "volume")
        parameters = {key: data[key] for key in keys if key in data}
        # Older agents must not receive new keywords, even when the options are disabled.
        for key in ("ownership_by_name", "skip_xattrs"):
            if data.get(key):
                parameters[key] = True

        operation = start_agent_operation(
            agent=agent,
            job=job,
            repository=repository,
            operation_type=AgentOperationType.restore,
            source=AgentOperationSource.manual,
            msg="Restore started",
            log_message="Restore queued",
            data={**parameters, "job_uuid": job_uuid},
        )

        response = AgentService.run_restore(
            agent,
            operation_uuid=operation.uuid,
            job_id=job.id if job else None,
            job_uuid=job_uuid,
            repository_id=repository.id,
            repository=cls._repository_payload(repository, agent),
            expected_job_tag=f"job_uuid:{job_uuid}" if job_uuid else None,
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
        operation = require_result(
            AgentOperation.query.join(Agent)
            .filter(AgentOperation.id == operation_id, Agent.user_id == user_id)
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
