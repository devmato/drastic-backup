"""Serve authenticated agent configuration queries and adapt incoming reports."""

from drastic_common.agent.commands import (
    BACKUP_RETRY_PROTOCOL,
    PATH_INCLUDE_EXCEPTIONS_PROTOCOL,
    RETENTION_RULES_PROTOCOL,
)
from drastic_common.backup_selection import requires_include_exceptions
from drastic_server.models.agent import (
    Agent,
    AgentOperation,
    AgentOperationType,
    AgentSession,
)
from drastic_server.schemas.repository import RepositorySchema
from drastic_server.services.operations.reports import AgentOperationService
from drastic_server.services.repository import (
    agent_repository_assignment,
    repository_password_envelope,
    store_agent_restic_access_key,
)


class AgentException(Exception):
    pass


class AgentRequestService:
    def __init__(self, agent):
        self.agent = agent

    @classmethod
    def get_by_sid(cls, sid):
        agent = Agent.query.join(AgentSession).filter(AgentSession.request_sid == sid).first()

        if agent:
            return cls(agent=agent)

        raise AgentException(f"Agent with sid: {sid} not found")

    def _repository_payload(self, repository):
        payload = RepositorySchema().dump(repository)
        assignment = agent_repository_assignment(self.agent, repository.id)
        if assignment and assignment.get("encrypted_restic_access_key"):
            payload["encrypted_restic_access_key"] = assignment["encrypted_restic_access_key"]
        return payload

    def _secret_envelope_payload(self, envelope):
        secret = envelope.user_secret
        return {
            "user_secret_id": secret.id,
            "type": secret.type,
            "encrypted_value": envelope.encrypted_value,
            "public_data": secret.public_data or {},
        }

    def _secret_envelopes_payload(self):
        envelopes = []
        seen = set()
        for repository in self.agent.repositories:
            envelope = repository_password_envelope(self.agent, repository)
            if envelope and envelope.id not in seen:
                envelopes.append(self._secret_envelope_payload(envelope))
                seen.add(envelope.id)
        return envelopes

    def _repositories_payload(self):
        return [self._repository_payload(repository) for repository in self.agent.repositories]

    def get_repositories(self):
        return self._repositories_payload()

    def retention_context(self, repository_id, include_policies=False, include_job_names=False):
        """Return current ownership, job membership and safety state, not backup history."""
        from drastic_server.models.job import Job
        from drastic_server.models.retention import Retention

        repository = next((item for item in self.agent.repositories
                           if item.id == int(repository_id) and item.user_id == self.agent.user_id), None)
        if repository is None:
            raise AgentException("Repository is not assigned to this agent")
        if (self.agent.protocol_version or 0) < RETENTION_RULES_PROTOCOL:
            raise AgentException(f"Update the agent for retention rules (protocol {RETENTION_RULES_PROTOCOL})")
        policy = Retention.query.filter_by(id=self.agent.user.default_retention_id, user_id=self.agent.user_id).first()
        jobs = Job.query.with_entities(Job.uuid, Job.name).join(Agent).filter(Agent.user_id == self.agent.user_id).all()
        context = {
            "owner_uuid": self.agent.user.backup_owner_uuid,
            "timezone": self.agent.user.effective_schedule_timezone,
            "running_operation_uuids": [uuid for (uuid,) in AgentOperation.query.with_entities(AgentOperation.uuid).filter_by(
                user_id=self.agent.user_id, repository_id=repository.id, type=AgentOperationType.backup,
                ).filter(AgentOperation.blocks_cleanup)],
            "check_token": repository.retention_check_token,
            "job_uuids": [job.uuid for job in jobs],
            "default": {"rules": policy.rules} if policy else None,
        }
        # Older agents validate exact context keys, so labels must be explicitly requested.
        if include_job_names:
            context["job_names"] = {job.uuid: job.name for job in jobs}
        if include_policies:
            policies = {item.id: {"rules": item.rules}
                        for item in Retention.query.filter_by(user_id=self.agent.user_id)}
            context["job_policies"] = {job.uuid: policies.get(job.effective_retention_id)
                for job in Job.query.join(Agent).filter(Agent.user_id == self.agent.user_id)}
        return context

    def sync(self):
        from drastic_server.models.job import Job, JobAction, JobSchedule
        from drastic_server.models.retention import Retention
        from drastic_server.schemas.sync import SyncSchema

        agent_jobs = Job.query.filter(Job.agent_id == self.agent.id).all()
        if (self.agent.protocol_version or 0) < 8 and any(
                job.type.name == "proxmox" and (job.config or {}).get("backup_mode", "snapshot") != "snapshot" for job in agent_jobs):
            raise AgentException("Update the agent before syncing native Proxmox jobs (protocol 8)")
        if (self.agent.protocol_version or 0) < 9 and any(
                job.type.name == "proxmox" and (job.config or {}).get("backup_mode", "snapshot") != "snapshot"
                and not job.config.get("fleecing_storage") for job in agent_jobs):
            raise AgentException("Update the agent before syncing automatic temporary storage selection (protocol 9)")
        if (self.agent.protocol_version or 0) < 14 and any(job.type.name == "truenas" for job in agent_jobs):
            raise AgentException("Update the agent before syncing TrueNAS path selection (protocol 14)")
        if (self.agent.protocol_version or 0) < PATH_INCLUDE_EXCEPTIONS_PROTOCOL and any(
                requires_include_exceptions(job.type.name, job.config) for job in agent_jobs):
            raise AgentException(f"Update the agent before syncing include exceptions (protocol {PATH_INCLUDE_EXCEPTIONS_PROTOCOL})")
        agent_schedules = JobSchedule.query.join(Job).filter(Job.agent_id == self.agent.id, JobSchedule.enabled).all()
        if (self.agent.protocol_version or 0) < BACKUP_RETRY_PROTOCOL and any(
                (schedule.config.get("retry") or {}).get("enabled") for schedule in agent_schedules):
            raise AgentException(f"Update the agent before syncing backup retries (protocol {BACKUP_RETRY_PROTOCOL})")
        if (self.agent.protocol_version or 0) < 12 and any(schedule.config.get("timing") for schedule in agent_schedules):
            raise AgentException("Update the agent before syncing schedule types (protocol 12)")

        if (self.agent.protocol_version or 0) < RETENTION_RULES_PROTOCOL:
            raise AgentException(f"Update the agent for retention rules (protocol {RETENTION_RULES_PROTOCOL})")
        return SyncSchema().dump(
            {
                "agent_uuid": self.agent.uuid,
                "backup_owner_uuid": self.agent.user.backup_owner_uuid,
                "schedule_timezone": self.agent.user.effective_schedule_timezone,
                "diagnostic_enabled": bool(self.agent.user.debug_token_hash),
                "repositories": self._repositories_payload(),
                "secret_envelopes": self._secret_envelopes_payload(),
                "jobs": agent_jobs,
                # Policies can also be used by chain-only jobs and pending cleanup.
                "retentions": Retention.query.filter(Retention.user_id == self.agent.user_id).all(),
                "schedules": agent_schedules,
                "actions": JobAction.query.join(Job).filter(Job.agent_id == self.agent.id).all(),
            }
        )

    def operation(self, operation_json):
        return AgentOperationService(self.agent).ingest(operation_json)

    def repository_recovery_envelope(self, repository_id):
        repository_id = int(repository_id)
        if not any(repository.id == repository_id for repository in self.agent.repositories):
            raise AgentException("Repository is not assigned to this agent")

        repository = next(
            (repository for repository in self.agent.repositories if repository.id == repository_id), None
        )
        if repository is None:
            raise AgentException("Repository is not assigned to this agent")

        envelope = repository_password_envelope(self.agent, repository)
        if not envelope:
            raise AgentException("Repository recovery envelope is not provisioned for this agent")

        return {"encrypted_recovery_key": envelope.encrypted_value}

    def store_repository_agent_key(self, repository_id, encrypted_agent_key, restic_key_id=None):
        repository_id = int(repository_id)
        if not any(repository.id == repository_id for repository in self.agent.repositories):
            raise AgentException("Repository is not assigned to this agent")

        assignment = agent_repository_assignment(self.agent, repository_id)
        if not assignment:
            raise AgentException("Repository recovery envelope is not provisioned for this agent")

        store_agent_restic_access_key(
            self.agent,
            repository_id,
            encrypted_agent_key,
            restic_key_id=restic_key_id,
        )

        from drastic_server.extensions import db

        db.session.commit()
        return {"ok": True}
