import copy
import json
from datetime import datetime
from uuid import uuid4

from cron_descriptor import get_description
from sqlalchemy import Enum
from sqlalchemy.ext.hybrid import hybrid_property

from drastic_common.agent.enums import AgentJobActionModule, AgentJobType
from drastic_server.extensions import db
from drastic_server.models.mixins import IdMixin, TimeMixin

JobType = AgentJobType
JobActionModuleEnum = AgentJobActionModule


class Job(IdMixin, TimeMixin, db.Model):
    __tablename__ = "jobs"
    uuid = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    name = db.Column(db.String(255), nullable=False)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False)
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

    @property
    def pathlist(self):
        return [path["path"] for path in self.config.get("paths", [])]

    @property
    def excludelist(self):
        return [exclude_pattern["path"] for exclude_pattern in self.config.get("exclude_patterns", [])]


class JobAction(IdMixin, TimeMixin, db.Model):
    __tablename__ = "job_actions"
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False)
    job = db.relationship(
        "Job",
        foreign_keys=[job_id],
        backref=db.backref(
            "actions", cascade="all,delete", order_by="desc(JobAction.hook)", uselist=True
        ),
    )
    module = db.Column(Enum(JobActionModuleEnum, native_enum=False, length=255), nullable=False)

    hook = db.Column(db.String(255), nullable=False)
    data = db.Column(db.JSON, default={}, nullable=False)

    def data_getter(self, key):
        if self.data and key in self.data:
            return self.data[key]

    def data_setter(self, key, value):
        if not self.data:
            self.data = {}
        self.data[key] = value

    @property
    def command(self):
        return self.data_getter("command")

    @command.setter
    def command(self, value):
        self.data_setter("command", value)

    @property
    def container(self):
        return self.data_getter("container")

    @container.setter
    def container(self, value):
        self.data_setter("container", value)

    @property
    def action(self):
        return self.data_getter("action")

    @action.setter
    def action(self, value):
        self.data_setter("action", value)


class JobSchedule(IdMixin, TimeMixin, db.Model):
    __tablename__ = "job_schedules"
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False)
    job = db.relationship(
        "Job",
        foreign_keys=[job_id],
        backref=db.backref("schedules", cascade="all,delete", uselist=True),
    )

    repository_id = db.Column(db.Integer, db.ForeignKey("repositories.id"), nullable=False)
    repository = db.relationship("Repository", foreign_keys=[repository_id], backref="schedules")

    retention_id = db.Column(db.Integer, db.ForeignKey("retentions.id"), nullable=True)
    retention = db.relationship("Retention", backref="job_schedules")

    enabled = db.Column(db.Boolean, default=False)
    advanced = db.Column(db.Boolean, default=False)
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
    def minute(self):
        if not self.advanced:
            return self.cron_string.split(" ")[0]

    @property
    def hour(self):
        if not self.advanced:
            return self.cron_string.split(" ")[1]

    @property
    def day_of_week(self):
        if not self.advanced:
            week_string = self.cron_string.split(" ")[-1]

            if week_string == "*":
                return [0, 1, 2, 3, 4, 5, 6]
            else:
                return json.loads(f"[{week_string}]")

    @property
    def cron_description(self):
        return get_description(self.cron_string)
