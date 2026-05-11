from drastic_server.models.agent import Agent, AgentRepositorySecret, AgentSession
from drastic_server.schemas.repository import RepositorySchema
from drastic_server.services.agent.operations import AgentOperationService


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
        secret = AgentRepositorySecret.query.filter(
            AgentRepositorySecret.agent_id == self.agent.id,
            AgentRepositorySecret.repository_id == repository.id,
        ).first()
        if secret and secret.encrypted_agent_key:
            payload["encrypted_agent_key"] = secret.encrypted_agent_key
        return payload

    def _repositories_payload(self):
        return [self._repository_payload(repository) for repository in self.agent.repositories]

    def get_repositories(self):
        return self._repositories_payload()

    def sync(self):
        from drastic_server.models.job import Job, JobAction, JobSchedule
        from drastic_server.models.retention import Retention
        from drastic_server.schemas.sync import SyncSchema

        return SyncSchema().dump(
            {
                "repositories": self._repositories_payload(),
                "jobs": Job.query.filter(Job.agent_id == self.agent.id).all(),
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

        secret = AgentRepositorySecret.query.filter(
            AgentRepositorySecret.agent_id == self.agent.id,
            AgentRepositorySecret.repository_id == repository_id,
        ).first()
        if not secret:
            raise AgentException("Repository recovery envelope is not provisioned for this agent")

        return {"encrypted_recovery_key": secret.encrypted_recovery_key}

    def store_repository_agent_key(self, repository_id, encrypted_agent_key):
        repository_id = int(repository_id)
        if not any(repository.id == repository_id for repository in self.agent.repositories):
            raise AgentException("Repository is not assigned to this agent")

        secret = AgentRepositorySecret.query.filter(
            AgentRepositorySecret.agent_id == self.agent.id,
            AgentRepositorySecret.repository_id == repository_id,
        ).first()
        if not secret:
            raise AgentException("Repository recovery envelope is not provisioned for this agent")

        secret.encrypted_agent_key = encrypted_agent_key
        secret.provisioned = True

        from drastic_server.extensions import db

        db.session.commit()
        return {"ok": True}


class AgentRequest(AgentRequestService):
    pass
