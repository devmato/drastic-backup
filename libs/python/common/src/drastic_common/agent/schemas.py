from datetime import datetime, timezone
from uuid import uuid4

from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates_schema

from drastic_common.agent.enums import (
    AgentJobActionModule,
    AgentJobType,
    AgentOperationLogLevel,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
    AgentRepositoryKind,
)

_JOB_TYPE_NAMES = tuple(AgentJobType.__members__)
_ACTION_MODULE_NAMES = tuple(AgentJobActionModule.__members__)
_PATH_GROUP_NAMES = ("file", "folder", "pattern")
_PROXMOX_SELECTION_MODES = ("all", "include")


class UTCDateTime(fields.NaiveDateTime):
    """Store UTC without an offset in SQL; always include it on the wire.

    Legacy values without an offset are treated as UTC, the container default.
    Their original timezone cannot be recovered from the stored value alone.
    """

    def __init__(self, **kwargs):
        super().__init__(timezone=timezone.utc, **kwargs)

    def _serialize(self, value, attr, obj, **kwargs):
        if value is not None:
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            value = value.astimezone(timezone.utc)
        return super()._serialize(value, attr, obj, **kwargs)


class AgentPathEntrySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    path = fields.String(required=True, validate=validate.Length(min=1))
    group = fields.String(load_default="folder", validate=validate.OneOf(_PATH_GROUP_NAMES))


class AgentFileBackupJobConfigSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    paths = fields.List(
        fields.Nested(AgentPathEntrySchema), required=True, validate=validate.Length(min=1)
    )
    exclude_patterns = fields.List(fields.Nested(AgentPathEntrySchema), load_default=list)


class AgentProxmoxBackupJobConfigSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    selection_mode = fields.String(
        load_default="all", validate=validate.OneOf(_PROXMOX_SELECTION_MODES)
    )
    guest_ids = fields.List(fields.Integer(validate=validate.Range(min=100)), load_default=list)
    exclude_guest_ids = fields.List(fields.Integer(validate=validate.Range(min=100)), load_default=list)
    backup_mode = fields.String(load_default="snapshot", validate=validate.OneOf(("snapshot", "native", "native_cbt")))
    fleecing_storage = fields.String(load_default="", validate=validate.Regexp(r"^(?:[A-Za-z][A-Za-z0-9_.-]*)?$"))

    @validates_schema
    def validate_guest_selection(self, data, **kwargs):
        if data.get("backup_mode", "snapshot") != "snapshot" and not data.get("fleecing_storage"):
            raise ValidationError({"fleecing_storage": ["Select a temporary backup storage for native backups."]})
        for field in ("guest_ids", "exclude_guest_ids"):
            guest_ids = data.get(field, [])
            if len(set(guest_ids)) != len(guest_ids):
                raise ValidationError({field: ["Duplicate guest IDs are not allowed."]})

        if data.get("selection_mode") == "include" and not data.get("guest_ids"):
            raise ValidationError(
                {"guest_ids": ["Select at least one guest when using include mode."]}
            )


class AgentRepositoryCheckConfigSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    enabled = fields.Boolean(load_default=False)
    read_data = fields.String(load_default=None, allow_none=True)

    @validates_schema
    def validate_read_data(self, data, **kwargs):
        if not data.get("enabled"):
            data["read_data"] = None
            return

        read_data = str(data.get("read_data") or "").strip() or None
        data["read_data"] = read_data


class AgentScheduleConfigSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    repository_check = fields.Nested(AgentRepositoryCheckConfigSchema, load_default=dict)


class AgentOperationLogSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    sequence = fields.Integer(required=True)
    level = fields.Enum(AgentOperationLogLevel, by_value=False, load_default=AgentOperationLogLevel.info)
    created = UTCDateTime(allow_none=True, load_default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    message = fields.String(required=True)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(allow_none=True), allow_none=True, load_default=None)


class AgentOperationArtifactSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    uuid = fields.String(load_default=lambda: str(uuid4()))
    artifact_key = fields.String(required=True)
    snapshot_id = fields.String(allow_none=True, load_default=None)
    state = fields.String(load_default="running")
    data = fields.Dict(keys=fields.String(), values=fields.Raw(allow_none=True), allow_none=True, load_default=dict)
    forgotten_at = UTCDateTime(allow_none=True, load_default=None)


class AgentOperationSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    uuid = fields.String(load_default=lambda: str(uuid4()))
    type = fields.Enum(AgentOperationType, by_value=False, required=True)
    state = fields.Enum(AgentOperationState, by_value=False, required=True)
    source = fields.Enum(AgentOperationSource, by_value=False, load_default=AgentOperationSource.manual)
    started = UTCDateTime(allow_none=True, load_default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    ended = UTCDateTime(allow_none=True, load_default=None)
    diagnostic_at = UTCDateTime()
    job_id = fields.Integer(allow_none=True, load_default=None)
    repository_id = fields.Integer(allow_none=True, load_default=None)
    schedule_id = fields.Integer(allow_none=True, load_default=None)
    retention_id = fields.Integer(allow_none=True, load_default=None)
    parent_operation_uuid = fields.String(allow_none=True, load_default=None)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(allow_none=True), load_default=dict)
    logs = fields.List(fields.Nested(AgentOperationLogSchema), load_default=list)
    artifacts = fields.List(fields.Nested(AgentOperationArtifactSchema), load_default=list)


class AgentRepositorySchema(Schema):
    id = fields.Integer(required=True)
    kind = fields.String(required=True, validate=validate.OneOf(tuple(kind.value for kind in AgentRepositoryKind)))
    location = fields.String(required=True)
    environment = fields.Dict(keys=fields.String(), values=fields.Raw(), load_default=dict, dump_default=dict)
    password_secret_id = fields.Integer(required=True)
    encrypted_restic_access_key = fields.Dict(keys=fields.String(), values=fields.Raw(), allow_none=True)


class AgentSecretEnvelopeSchema(Schema):
    user_secret_id = fields.Integer(required=True)
    type = fields.String(required=True)
    encrypted_value = fields.Dict(keys=fields.String(), values=fields.Raw(), required=True)
    public_data = fields.Dict(keys=fields.String(), values=fields.Raw(), load_default=dict, dump_default=dict)


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
    diagnostic_enabled = fields.Boolean(load_default=False, dump_default=False)
    repositories = fields.List(fields.Nested(AgentRepositorySchema), required=True)
    secret_envelopes = fields.List(fields.Nested(AgentSecretEnvelopeSchema), load_default=list)
    retentions = fields.List(fields.Nested(AgentRetentionSchema), required=True)
    jobs = fields.List(fields.Nested(AgentJobSchema), required=True)
    actions = fields.List(fields.Nested(AgentJobActionSchema), required=True)
    schedules = fields.List(fields.Nested(AgentJobScheduleSchema), required=True)


AgentReportSchema = AgentOperationSchema


def _value_for(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
