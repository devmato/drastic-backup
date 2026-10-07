import copy
from datetime import datetime
from uuid import uuid4

from cron_descriptor import get_description
from sqlalchemy import Enum
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.mutable import MutableDict

from drastic_common.agent.enums import AgentJobActionModule, AgentJobType
from drastic_common.scheduling import describe, timing_from_cron
from drastic_server.extensions import db
from drastic_server.models.mixins import IdMixin, TimeMixin

JobType = AgentJobType
JobActionModuleEnum = AgentJobActionModule


class Job(IdMixin, TimeMixin, db.Model):
    __tablename__ = "jobs"
    uuid = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    name = db.Column(db.String(255), nullable=False)
    agent_id = db.Column(
        db.Integer,
        db.ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type = db.Column(Enum(JobType, native_enum=False, length=255), nullable=False)
    _config = db.Column("config", db.JSON, nullable=False, default=lambda: {"data": {}})
    agent = db.relationship("Agent", foreign_keys=[agent_id], backref=db.backref("jobs"))

    @hybrid_property
    def last_operation(self):
        backup_operations = [
            operation for operation in self.operations if getattr(operation.type, "name", None) == "backup"
        ]
        if len(backup_operations) > 0:
            return max(
                backup_operations,
                key=lambda operation: (operation.started or datetime.min, operation.id or 0),
            )

    @property
    def type_text(self):
        return self.type.value["text"] if self.type else None

    @property
    def config(self):
        return copy.deepcopy((self._config or {}).get("data") or {})

    @config.setter
    def config(self, value):
        self.config_envelope = {"data": value or {}}

    @property
    def config_envelope(self):
        return copy.deepcopy(self._config or {"data": {}})

    @config_envelope.setter
    def config_envelope(self, value):
        self._config = {"data": copy.deepcopy((value or {}).get("data") or {})}


class JobAction(IdMixin, TimeMixin, db.Model):
    __tablename__ = "job_actions"
    job_id = db.Column(
        db.Integer,
        db.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job = db.relationship(
        "Job",
        foreign_keys=[job_id],
        backref=db.backref(
            "actions", cascade="all,delete", order_by="desc(JobAction.hook)", uselist=True
        ),
    )
    module = db.Column(Enum(JobActionModuleEnum, native_enum=False, length=255), nullable=False)

    hook = db.Column(db.String(255), nullable=False)
    data = db.Column(MutableDict.as_mutable(db.JSON), default=dict, nullable=False)


class JobSchedule(IdMixin, TimeMixin, db.Model):
    __tablename__ = "job_schedules"
    job_id = db.Column(
        db.Integer,
        db.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job = db.relationship(
        "Job",
        foreign_keys=[job_id],
        backref=db.backref("schedules", cascade="all,delete", uselist=True),
    )

    repository_id = db.Column(db.Integer, db.ForeignKey("repositories.id"), nullable=False)
    repository = db.relationship("Repository", foreign_keys=[repository_id], backref="schedules")

    retention_id = db.Column(
        db.Integer,
        db.ForeignKey("retentions.id", ondelete="SET NULL"),
        nullable=True,
    )
    retention = db.relationship("Retention", backref="job_schedules")

    enabled = db.Column(db.Boolean, nullable=False, default=False)
    advanced = db.Column(db.Boolean, nullable=False, default=False)
    _config = db.Column("config", db.JSON, nullable=False, default=lambda: {"data": {}})

    cron_string = db.Column(db.String(255), nullable=False)

    @property
    def config(self):
        return copy.deepcopy((self._config or {}).get("data") or {})

    @config.setter
    def config(self, value):
        self.config_envelope = {"data": value or {}}

    @property
    def config_envelope(self):
        return copy.deepcopy(self._config or {"data": {}})

    @config_envelope.setter
    def config_envelope(self, value):
        self._config = {"data": copy.deepcopy((value or {}).get("data") or {})}

    @property
    def cron_description(self):
        if self.config.get("timing"):
            return describe(self.config["timing"])
        return get_description(self.cron_string)

    @property
    def timing(self):
        return self.config.get("timing") or timing_from_cron(self.cron_string)
