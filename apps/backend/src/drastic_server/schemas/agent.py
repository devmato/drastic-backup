from marshmallow import EXCLUDE, Schema, fields, validate

from drastic_common.agent.enums import (
    AgentOperationLogLevel,
    AgentOperationSource,
    AgentOperationState,
    AgentOperationType,
)
from drastic_common.agent.schemas import AgentOperationSchema, UTCDateTime
from drastic_common.proxmox import ProxmoxSettingsSchema, validate_proxmox_token_secret
from drastic_common.truenas import AgentConnectionsSchema, TrueNASSettingsSchema, validate_api_key
from drastic_common.version import get_version
from drastic_server.schemas.repository import RepositoryResponseSchema

_OPERATION_TYPE_NAMES = tuple(AgentOperationType.__members__)
_OPERATION_STATE_NAMES = tuple(AgentOperationState.__members__)
_OPERATION_SOURCE_NAMES = tuple(AgentOperationSource.__members__)
_OPERATION_LOG_LEVEL_NAMES = tuple(AgentOperationLogLevel.__members__)

__all__ = ["AgentOperationSchema"]


class AgentResponseSchema(Schema):
    id = fields.Integer(required=True)
    uuid = fields.UUID(required=True)
    hostname = fields.String(allow_none=True)
    alias = fields.String(allow_none=True)
    display_name = fields.String(required=True)
    os = fields.String(allow_none=True)
    version = fields.String(allow_none=True)
    backend_version = fields.Function(lambda _: get_version())
    protocol_version = fields.Integer(required=True)
    connections = fields.Nested(AgentConnectionsSchema, allow_none=True)
    install_type = fields.String(required=True)
    ssh_public_key = fields.String(allow_none=True)
    ssh_key_fingerprint = fields.String(allow_none=True)
    ssh_key_algorithm = fields.String(allow_none=True)
    online = fields.Boolean(required=True)
    repositories = fields.List(fields.Nested(RepositoryResponseSchema), required=True)
    last_connection = fields.DateTime(allow_none=True)
    created = fields.DateTime(allow_none=True)


class AgentUpdateInputSchema(Schema):
    alias = fields.String(required=True, allow_none=True, validate=validate.Length(max=255))


class AgentRepositoryAssignInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    repository_ids = fields.List(fields.Integer(), load_default=list)
    recovery_key = fields.String(load_default=None, allow_none=True)


class AgentSyncInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    recovery_key = fields.String(load_default=None, allow_none=True)


class AgentProxmoxSettingsInputSchema(ProxmoxSettingsSchema):
    token_secret = fields.String(load_only=True, validate=validate_proxmox_token_secret)


class AgentProxmoxSettingsResponseSchema(ProxmoxSettingsSchema):
    token_secret_configured = fields.Boolean(required=True)
    configured = fields.Boolean(required=True)
    source = fields.String(required=True)


class AgentProxmoxTestResponseSchema(Schema):
    node = fields.String(required=True)
    guest_count = fields.Integer(required=True)


class AgentTrueNASSettingsInputSchema(TrueNASSettingsSchema):
    api_key = fields.String(load_only=True, validate=validate_api_key)


class AgentTrueNASSettingsResponseSchema(TrueNASSettingsSchema):
    api_key_configured = fields.Boolean(required=True)
    configured = fields.Boolean(required=True)
    pending_snapshots = fields.Integer(dump_default=0)


class AgentTrueNASTestResponseSchema(Schema):
    version = fields.String(required=True)
    dataset_count = fields.Integer(required=True)


class TrueNASDatasetSchema(Schema):
    id = fields.String(required=True)
    mountpoint = fields.String(allow_none=True)
    path = fields.String(allow_none=True)
    available = fields.Boolean(required=True)
    error = fields.String(required=True)


class TrueNASDatasetsResponseSchema(Schema):
    datasets = fields.List(fields.Nested(TrueNASDatasetSchema), required=True)


class AgentOperationLogResponseSchema(Schema):
    id = fields.Integer(required=True)
    sequence = fields.Integer(required=True)
    level = fields.Method("get_level")
    message = fields.String(required=True)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), allow_none=True)
    created = UTCDateTime(allow_none=True)

    def get_level(self, obj):
        return obj.level.name if obj.level else None


class AgentOperationArtifactResponseSchema(Schema):
    id = fields.Integer(required=True)
    uuid = fields.String(required=True)
    artifact_key = fields.String(required=True)
    snapshot_id = fields.String(allow_none=True)
    state = fields.String(required=True)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), allow_none=True)
    forgotten_at = UTCDateTime(allow_none=True)


class AgentOperationResponseSchema(Schema):
    id = fields.Integer(required=True)
    uuid = fields.String(required=True)
    agent_id = fields.Integer(required=True)
    repository_id = fields.Integer(allow_none=True)
    job_id = fields.Integer(allow_none=True)
    schedule_id = fields.Integer(allow_none=True)
    retention_id = fields.Integer(allow_none=True)
    parent_operation_id = fields.Integer(allow_none=True)
    state = fields.Method("get_state")
    type = fields.Method("get_type")
    type_text = fields.Method("get_type_text")
    source = fields.Method("get_source")
    started = UTCDateTime(allow_none=True)
    ended = UTCDateTime(allow_none=True)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), allow_none=True)
    log = fields.String(attribute="log", allow_none=True)
    logs = fields.List(fields.Nested(AgentOperationLogResponseSchema), required=True)
    artifacts = fields.List(fields.Nested(AgentOperationArtifactResponseSchema), required=True)

    def get_state(self, obj):
        return obj.state.name if obj.state else None

    def get_type(self, obj):
        return obj.type.name if obj.type else None

    def get_type_text(self, obj):
        return obj.type.value["text"] if obj.type else None

    def get_source(self, obj):
        return obj.source.name if obj.source else None


class AgentOperationDetailResponseSchema(AgentOperationResponseSchema):
    agent_hostname = fields.String(attribute="agent.hostname", dump_default=None, allow_none=True)
    agent_display_name = fields.String(attribute="agent.display_name", dump_default=None, allow_none=True)
    job_name = fields.String(attribute="job.name", dump_default=None, allow_none=True)
    repository_name = fields.String(attribute="repository.name", dump_default=None, allow_none=True)


class AgentOperationStartResponseSchema(Schema):
    msg = fields.String(required=True)
    id = fields.Integer(required=True)
    operation_id = fields.Integer(required=True)
    operation_uuid = fields.String(required=True)
    agent_id = fields.Integer(required=True)
    repository_id = fields.Integer(allow_none=True)
    job_id = fields.Integer(allow_none=True)
    type = fields.String(required=True)
    state = fields.String(required=True)
    success = fields.Boolean(load_default=True, dump_default=True)
    dispatch_status = fields.String(required=True)


class AgentOperationQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    type = fields.Enum(AgentOperationType, by_value=False, load_default=None, allow_none=True)
    state = fields.Enum(AgentOperationState, by_value=False, load_default=None, allow_none=True)
    job_id = fields.Integer(load_default=None, allow_none=True)


class AgentRegisterInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    username = fields.String(required=True, validate=validate.Length(min=1))
    password = fields.String(required=True, validate=validate.Length(min=1))
    hostname = fields.String(required=False, allow_none=True)
    os = fields.String(required=False, allow_none=True)
    version = fields.String(required=False, allow_none=True)
    platform = fields.String(required=False, allow_none=True)
    deployment = fields.String(required=False, allow_none=True)
    install_type = fields.String(required=False, allow_none=True)
    public_key = fields.String(required=False, allow_none=True)
    ssh_public_key = fields.String(required=False, allow_none=True)


class AgentRegisterResponseSchema(Schema):
    identifier = fields.Integer(required=True)
    uuid = fields.UUID(required=True)
    secret = fields.String(required=True)
    server_url = fields.String(required=True)


class AgentInstallTargetSchema(Schema):
    id = fields.String(required=True)
    label = fields.String(required=True)
    platform = fields.String(required=True)
    os = fields.String(required=True)
    arch = fields.String(required=True)
    deployment = fields.String(required=True)
    description = fields.String(required=True)
    artifact_type = fields.String(allow_none=True)
    image = fields.String(allow_none=True)


class AgentInstallOptionsResponseSchema(Schema):
    server_url = fields.String(allow_none=True)
    targets = fields.List(fields.Nested(AgentInstallTargetSchema), required=True)


class AgentOperationOptionsResponseSchema(Schema):
    operation_types = fields.List(fields.Dict(keys=fields.String(), values=fields.Raw()), required=True)
    operation_states = fields.List(fields.Dict(keys=fields.String(), values=fields.Raw()), required=True)
