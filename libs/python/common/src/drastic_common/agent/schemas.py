from datetime import datetime
from uuid import uuid4

from marshmallow import EXCLUDE, Schema, fields

from drastic_common.agent.enums import (
    AgentOperationLogLevel,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)


class AgentOperationLogSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    sequence = fields.Integer(required=True)
    level = fields.Enum(AgentOperationLogLevel, by_value=False, load_default=AgentOperationLogLevel.info)
    created = fields.DateTime(allow_none=True, load_default=datetime.now)
    message = fields.String(required=True)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), allow_none=True, load_default=None)


class AgentOperationArtifactSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    uuid = fields.String(load_default=lambda: str(uuid4()))
    artifact_key = fields.String(required=True)
    snapshot_id = fields.String(allow_none=True, load_default=None)
    state = fields.String(load_default="running")
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), allow_none=True, load_default=dict)
    forgotten_at = fields.DateTime(allow_none=True, load_default=None)


class AgentOperationSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    uuid = fields.String(load_default=lambda: str(uuid4()))
    type = fields.Enum(AgentOperationType, by_value=False, required=True)
    state = fields.Enum(AgentOperationState, by_value=False, required=True)
    source = fields.Enum(AgentOperationSource, by_value=False, load_default=AgentOperationSource.manual)
    started = fields.DateTime(allow_none=True, load_default=datetime.now)
    ended = fields.DateTime(allow_none=True, load_default=None)
    job_id = fields.Integer(allow_none=True, load_default=None)
    repository_id = fields.Integer(allow_none=True, load_default=None)
    schedule_id = fields.Integer(allow_none=True, load_default=None)
    retention_id = fields.Integer(allow_none=True, load_default=None)
    parent_operation_uuid = fields.String(allow_none=True, load_default=None)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), load_default=dict)
    logs = fields.List(fields.Nested(AgentOperationLogSchema), load_default=list)
    artifacts = fields.List(fields.Nested(AgentOperationArtifactSchema), load_default=list)


class AgentRepositorySchema(Schema):
    id = fields.Integer(required=True)
    kind = fields.String(required=True)
    location = fields.String(required=True)
    environment = fields.Dict(keys=fields.String(), values=fields.Raw(), load_default=dict, dump_default=dict)
    encrypted_agent_key = fields.Dict(keys=fields.String(), values=fields.Raw(), allow_none=True)


class AgentRetentionSchema(Schema):
    id = fields.Integer(required=True)
    name = fields.String(required=True)
    keep_last = fields.Integer(allow_none=True)
    keep_hourly = fields.Integer(allow_none=True)
    keep_weekly = fields.Integer(allow_none=True)
    keep_monthly = fields.Integer(allow_none=True)
    keep_yearly = fields.Integer(allow_none=True)


class AgentJobSchema(Schema):
    id = fields.Integer(required=True)
    uuid = fields.String(required=True)
    type = fields.Method("get_type")
    config = fields.Method("get_config")

    def get_type(self, obj):
        job_type = _value_for(obj, "type")
        return job_type.name if hasattr(job_type, "name") else job_type

    def get_config(self, obj):
        return _value_for(obj, "config") or {}


class AgentJobActionSchema(Schema):
    id = fields.Integer(required=True)
    job_id = fields.Integer(required=True)
    module = fields.Method("get_module")
    hook = fields.String(required=True)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), required=True)

    def get_module(self, obj):
        module = _value_for(obj, "module")
        return module.name if hasattr(module, "name") else module


class AgentJobScheduleSchema(Schema):
    id = fields.Integer(required=True)
    job_id = fields.Integer(required=True)
    enabled = fields.Boolean(required=True)
    repository_id = fields.Integer(required=True)
    retention_id = fields.Integer(allow_none=True)
    cron_string = fields.String(required=True)
    config = fields.Method("get_config")

    def get_config(self, obj):
        return _value_for(obj, "config") or {}


class AgentSyncSchema(Schema):
    repositories = fields.List(fields.Nested(AgentRepositorySchema), required=True)
    retentions = fields.List(fields.Nested(AgentRetentionSchema), required=True)
    jobs = fields.List(fields.Nested(AgentJobSchema), required=True)
    actions = fields.List(fields.Nested(AgentJobActionSchema), required=True)
    schedules = fields.List(fields.Nested(AgentJobScheduleSchema), required=True)


AgentReportSchema = AgentOperationSchema


def _value_for(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
