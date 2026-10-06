from drastic_server.models.agent import Agent, AgentSession
from drastic_server.schemas.repository import RepositorySchema
from drastic_server.services.agent.operations import AgentOperationService
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
        if (self.agent.protocol_version or 0) < 3 and any(job.type.name == "truenas" for job in agent_jobs):
            raise AgentException("Update the agent before syncing TrueNAS jobs")

        return SyncSchema().dump(
            {
                "diagnostic_enabled": bool(self.agent.user.debug_token_hash),
                "repositories": self._repositories_payload(),
                "secret_envelopes": self._secret_envelopes_payload(),
                "jobs": agent_jobs,
                "retentions": Retention.query.join(JobSchedule)
                .join(Job, JobSchedule.job_id == Job.id)
                .filter(Job.agent_id == self.agent.id)
                .distinct()
                .all(),
                "schedules": JobSchedule.query.join(Job)
                .filter(Job.agent_id == self.agent.id, JobSchedule.enabled)
                .all(),
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
