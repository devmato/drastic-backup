import secrets
from uuid import uuid4

from sqlalchemy import Enum
from sqlalchemy.ext.hybrid import hybrid_property

from drastic_common.agent.enums import (
    AgentOperationLogLevel,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_server.extensions import db
from drastic_server.models.mixins import IdMixin, TimeMixin

# import drastic_server.utils.agent


agent_repositories = db.Table(
    "agent_repositories",
    db.Column("agent_id", db.Integer, db.ForeignKey("agents.id"), primary_key=True),
    db.Column("repository_id", db.Integer, db.ForeignKey("repositories.id"), primary_key=True),
    db.Column("encrypted_restic_access_key", db.JSON, nullable=True),
    db.Column("restic_key_id", db.String(255), nullable=True),
    db.Column("provisioned", db.Boolean, nullable=False, default=False),
)


class Agent(IdMixin, TimeMixin, db.Model):
    __tablename__ = "agents"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("agents"))
    secret = db.Column(db.String(255), nullable=False)
    public_key = db.Column(db.Text, nullable=True)
    ssh_public_key = db.Column(db.Text, nullable=True)
    ssh_key_fingerprint = db.Column(db.String(255), nullable=True)
    ssh_key_algorithm = db.Column(db.String(64), nullable=True)
    os = db.Column(db.String(255), nullable=True)
    version = db.Column(db.String(255), nullable=True)
    install_type = db.Column(db.String(32), nullable=False, default="manual")
    hostname = db.Column(db.String(255), nullable=True)
    last_connection = db.Column(db.DateTime, nullable=True)
    repositories = db.relationship(
        "Repository",
        secondary=agent_repositories,
        backref=db.backref("agents"),
    )
    # Online property
    @hybrid_property
    def online(self):
        return True if self.session else False

    # Generate secret for new Agent
    @staticmethod
    def generate_secret():
        return secrets.token_urlsafe(32)

    @staticmethod
    def normalize_install_type(value):
        normalized = str(value or "").strip().lower()
        if normalized in {"docker", "release", "git", "manual"}:
            return normalized
        return "manual"

    # Expose AgentCommand instance as property
    # @property
    # def command(self):
    #     drastic_server.utils.agent
    #     return drastic_server.utils.agent.agent.AgentCommand(self)


class AgentSession(IdMixin, TimeMixin, db.Model):
    __tablename__ = "agent_sessions"
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False)
    agent = db.relationship(
        "Agent", foreign_keys=[agent_id], backref=db.backref("session", uselist=False)
    )
    request_sid = db.Column(db.String(255), nullable=False)


class AgentOperation(IdMixin, TimeMixin, db.Model):
    __tablename__ = "agent_operations"
    uuid = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False)
    agent = db.relationship("Agent", foreign_keys=[agent_id], backref=db.backref("operations"))
    repository_id = db.Column(db.Integer, db.ForeignKey("repositories.id"), nullable=True)
    repository = db.relationship(
        "Repository", foreign_keys=[repository_id], backref=db.backref("operations")
    )
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=True)
    job = db.relationship("Job", foreign_keys=[job_id], backref=db.backref("operations"))
    schedule_id = db.Column(db.Integer, db.ForeignKey("job_schedules.id"), nullable=True)
    schedule = db.relationship("JobSchedule", foreign_keys=[schedule_id], backref=db.backref("operations"))
    retention_id = db.Column(db.Integer, db.ForeignKey("retentions.id"), nullable=True)
    retention = db.relationship("Retention", foreign_keys=[retention_id], backref=db.backref("operations"))
    parent_operation_id = db.Column(db.Integer, db.ForeignKey("agent_operations.id"), nullable=True)
    parent_operation = db.relationship(
        "AgentOperation",
        remote_side="AgentOperation.id",
        backref=db.backref("child_operations"),
    )
    data = db.Column(db.JSON, nullable=True)
    state = db.Column(Enum(AgentOperationState, native_enum=False, length=255), nullable=False)
    type = db.Column(Enum(AgentOperationType, native_enum=False, length=255), nullable=False)
    source = db.Column(
        Enum(AgentOperationSource, native_enum=False, length=255),
        nullable=False,
        default=AgentOperationSource.manual,
    )
    started = db.Column(db.DateTime, nullable=True)
    ended = db.Column(db.DateTime, nullable=True)

    @property
    def log(self):
        return "\n".join(log.message for log in sorted(self.logs, key=lambda item: item.sequence or 0))


class AgentOperationLog(IdMixin, TimeMixin, db.Model):
    __tablename__ = "agent_operation_logs"
    __table_args__ = (
        db.UniqueConstraint("operation_id", "sequence", name="uq_agent_operation_logs_operation_sequence"),
    )

    operation_id = db.Column(db.Integer, db.ForeignKey("agent_operations.id"), nullable=False)
    operation = db.relationship(
        "AgentOperation",
        foreign_keys=[operation_id],
        backref=db.backref("logs", cascade="all, delete-orphan"),
    )
    sequence = db.Column(db.Integer, nullable=False)
    level = db.Column(Enum(AgentOperationLogLevel, native_enum=False, length=255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    data = db.Column(db.JSON, nullable=True)


class AgentOperationArtifact(IdMixin, TimeMixin, db.Model):
    __tablename__ = "agent_operation_artifacts"
    __table_args__ = (
        db.UniqueConstraint("operation_id", "artifact_key", name="uq_agent_operation_artifacts_operation_key"),
    )

    uuid = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    operation_id = db.Column(db.Integer, db.ForeignKey("agent_operations.id"), nullable=False)
    operation = db.relationship(
        "AgentOperation",
        foreign_keys=[operation_id],
        backref=db.backref("artifacts", cascade="all, delete-orphan"),
    )
    artifact_key = db.Column(db.String(255), nullable=False)
    snapshot_id = db.Column(db.String(255), nullable=True)
    state = db.Column(db.String(32), nullable=False)
    data = db.Column(db.JSON, nullable=True)
    forgotten_at = db.Column(db.DateTime, nullable=True)
