from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates_schema

from drastic_server.models.job import JobActionModuleEnum, JobType

_JOB_TYPE_NAMES = tuple(JobType.__members__)
_ACTION_MODULE_NAMES = tuple(JobActionModuleEnum.__members__)
_ACTION_HOOK_NAMES = ("start", "error", "success", "end")
_PATH_GROUP_NAMES = ("file", "folder", "pattern")
_DOCKER_ACTION_NAMES = ("stop", "start", "command")
_PROXMOX_SELECTION_MODES = ("all", "include")
_PROXMOX_GUEST_TYPES = ("qemu",)


class PathEntrySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    path = fields.String(required=True, validate=validate.Length(min=1))
    group = fields.String(load_default="folder", validate=validate.OneOf(_PATH_GROUP_NAMES))


class FileBackupJobConfigSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    paths = fields.List(
        fields.Nested(PathEntrySchema), required=True, validate=validate.Length(min=1)
    )
    exclude_patterns = fields.List(fields.Nested(PathEntrySchema), load_default=list)


class ProxmoxBackupJobConfigSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    selection_mode = fields.String(
        load_default="all", validate=validate.OneOf(_PROXMOX_SELECTION_MODES)
    )
    guest_ids = fields.List(fields.Integer(validate=validate.Range(min=100)), load_default=list)

    @validates_schema
    def validate_guest_selection(self, data, **kwargs):
        guest_ids = data.get("guest_ids", [])

        if len(set(guest_ids)) != len(guest_ids):
            raise ValidationError({"guest_ids": ["Duplicate guest IDs are not allowed."]})

        if data.get("selection_mode") == "include" and not guest_ids:
            raise ValidationError(
                {"guest_ids": ["Select at least one guest when using include mode."]}
            )


class RepositoryCheckConfigSchema(Schema):
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


class ScheduleConfigSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    repository_check = fields.Nested(RepositoryCheckConfigSchema, load_default=dict)


class JobActionSchema(Schema):
    id = fields.Integer(required=True)
    job_id = fields.Integer(required=True)
    module = fields.Method("get_module")
    hook = fields.String(required=True)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), required=True)

    def get_module(self, obj):
        return obj.module.name if obj.module else None


class JobScheduleSchema(Schema):
    id = fields.Integer(required=True)
    job_id = fields.Integer(required=True)
    enabled = fields.Boolean(required=True)
    repository_id = fields.Integer(required=True)
    retention_id = fields.Integer(allow_none=True)
    cron_string = fields.String(required=True)
    config = fields.Method("get_config")

    def get_config(self, obj):
        return obj.config


class JobSchema(Schema):
    id = fields.Integer(required=True)
    uuid = fields.String(required=True)
    type = fields.Method("get_type")
    config = fields.Method("get_config")

    def get_type(self, obj):
        return obj.type.name if obj.type else None

    def get_config(self, obj):
        return obj.config


class JobScheduleResponseSchema(Schema):
    id = fields.Integer(required=True)
    enabled = fields.Boolean(required=True)
    cron_string = fields.String(required=True)
    cron_description = fields.String(attribute="cron_description", required=True)
    repository_id = fields.Integer(required=True)
    retention_id = fields.Integer(allow_none=True)
    config = fields.Method("get_config")
    repository_name = fields.Method("get_repository_name")
    repository_location = fields.Method("get_repository_location")
    retention_name = fields.Method("get_retention_name")

    def get_repository_name(self, obj):
        return obj.repository.name if obj.repository else None

    def get_config(self, obj):
        return obj.config

    def get_repository_location(self, obj):
        return obj.repository.public_location if obj.repository else None

    def get_retention_name(self, obj):
        return obj.retention.name if obj.retention else None


class JobActionResponseSchema(Schema):
    id = fields.Integer(required=True)
    hook = fields.String(required=True)
    module = fields.Method("get_module")
    module_text = fields.Method("get_module_text")
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), required=True)

    def get_module(self, obj):
        return obj.module.name if obj.module else None

    def get_module_text(self, obj):
        return obj.module.value["text"] if obj.module else None


class JobLastOperationSchema(Schema):
    id = fields.Integer(required=True)
    state = fields.Method("get_state")
    started = fields.DateTime(allow_none=True)
    ended = fields.DateTime(allow_none=True)

    def get_state(self, obj):
        return obj.state.name if obj.state else None


class JobResponseSchema(Schema):
    id = fields.Integer(required=True)
    uuid = fields.String(required=True)
    name = fields.String(required=True)
    agent_id = fields.Integer(required=True)
    agent_hostname = fields.Method("get_agent_hostname")
    agent_online = fields.Method("get_agent_online")
    agent_repositories = fields.Method("get_agent_repositories")
    type = fields.Method("get_type")
    type_text = fields.String(attribute="type_text", allow_none=True)
    config = fields.Method("get_config")
    schedules = fields.List(fields.Nested(JobScheduleResponseSchema), required=True)
    actions = fields.List(fields.Nested(JobActionResponseSchema), required=True)
    last_operation = fields.Nested(JobLastOperationSchema, allow_none=True)
    created = fields.DateTime(allow_none=True)

    def get_agent_hostname(self, obj):
        return obj.agent.hostname if obj.agent else None

    def get_agent_online(self, obj):
        return obj.agent.online if obj.agent else False

    def get_agent_repositories(self, obj):
        if not obj.agent:
            return []

        return [
            {
                "id": repository.id,
                "name": repository.name,
                "location": repository.public_location,
            }
            for repository in obj.agent.repositories
        ]

    def get_type(self, obj):
        return obj.type.name if obj.type else None

    def get_config(self, obj):
        return obj.config


class AgentJobsResponseSchema(Schema):
    id = fields.Integer(required=True)
    hostname = fields.String(allow_none=True)
    online = fields.Boolean(required=True)
    repositories = fields.Method("get_repositories")
    jobs = fields.List(fields.Nested(JobResponseSchema), required=True)

    def get_repositories(self, obj):
        return [
            {
                "id": repository.id,
                "name": repository.name,
                "location": repository.public_location,
            }
            for repository in obj.repositories
        ]


class JobCreateInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    agent_id = fields.Integer(required=True)
    name = fields.String(required=True, validate=validate.Length(min=1))
    type = fields.String(required=True, validate=validate.OneOf(_JOB_TYPE_NAMES))
    config = fields.Dict(required=True)

    @validates_schema
    def validate_config(self, data, **kwargs):
        job_type = data.get("type")
        config = data.get("config") or {}

        try:
            if job_type == "file":
                data["config"] = FileBackupJobConfigSchema().load(config)
            elif job_type == "proxmox":
                data["config"] = ProxmoxBackupJobConfigSchema().load(config)
        except ValidationError as exc:
            raise ValidationError({"config": exc.messages}) from exc


class JobUpdateInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    name = fields.String(validate=validate.Length(min=1))
    config = fields.Dict()


class JobOperationRunInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    repository_id = fields.Integer(required=True)
    recovery_key = fields.String(load_default=None, allow_none=True)
    options = fields.Dict(load_default=dict)

    @validates_schema
    def validate_options(self, data, **kwargs):
        try:
            data["options"] = ScheduleConfigSchema().load(data.get("options") or {})
        except ValidationError as exc:
            raise ValidationError({"options": exc.messages}) from exc


class ScheduleCreateInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    minute = fields.String(required=True, validate=validate.Regexp(r"^(?:[0-5]?\d)$"))
    hour = fields.String(required=True, validate=validate.Regexp(r"^(?:[01]?\d|2[0-3])$"))
    day_of_week = fields.List(
        fields.Integer(validate=validate.Range(min=0, max=6)),
        required=True,
        validate=validate.Length(min=1, max=7),
    )
    enabled = fields.Boolean(required=True)
    repository_id = fields.Integer(required=True)
    retention_id = fields.Integer(allow_none=True, load_default=None)
    recovery_key = fields.String(load_default=None, allow_none=True)
    config = fields.Dict(load_default=dict)

    @validates_schema
    def validate_day_of_week(self, data, **kwargs):
        day_of_week = data["day_of_week"]
        if len(set(day_of_week)) != len(day_of_week):
            raise ValidationError({"day_of_week": ["Duplicate weekdays are not allowed."]})

        try:
            data["config"] = ScheduleConfigSchema().load(data.get("config") or {})
        except ValidationError as exc:
            raise ValidationError({"config": exc.messages}) from exc


class ScheduleUpdateInputSchema(ScheduleCreateInputSchema):
    pass


class ActionCreateInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    module = fields.String(required=True, validate=validate.OneOf(_ACTION_MODULE_NAMES))
    hook = fields.String(required=True, validate=validate.OneOf(_ACTION_HOOK_NAMES))
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), load_default=dict)

    @validates_schema
    def validate_action_data(self, data, **kwargs):
        payload = data.get("data") or {}

        if data["module"] == "command":
            command = str(payload.get("command") or "").strip()
            if not command:
                raise ValidationError(
                    {"data": ["Command actions require a non-empty data.command value."]}
                )
            return

        container = str(payload.get("container") or "").strip()
        action = payload.get("action")
        if not container:
            raise ValidationError(
                {"data": ["Docker actions require a non-empty data.container value."]}
            )
        if action not in _DOCKER_ACTION_NAMES:
            raise ValidationError(
                {
                    "data": [
                        "Docker actions require data.action to be one of stop, start or command."
                    ]
                }
            )
        if action == "command" and not str(payload.get("command") or "").strip():
            raise ValidationError(
                {"data": ["Docker command actions require a non-empty data.command value."]}
            )


class ActionUpdateInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    module = fields.String(validate=validate.OneOf(_ACTION_MODULE_NAMES))
    hook = fields.String(validate=validate.OneOf(_ACTION_HOOK_NAMES))
    data = fields.Dict(keys=fields.String(), values=fields.Raw())


class DirlistQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    agent_id = fields.Integer(required=True)
    base_directory = fields.String(load_default="/")


class DirectoryEntrySchema(Schema):
    name = fields.String(required=True)
    file = fields.Boolean(required=True)
    size = fields.Float(required=True)
    readable = fields.Boolean(required=True)


class DirlistResponseSchema(Schema):
    base_directory = fields.String(required=True)
    parent_directory = fields.String(required=True)
    directories = fields.List(fields.Nested(DirectoryEntrySchema), required=True)


class ContainersQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    agent_id = fields.Integer(required=True)


class ContainersResponseSchema(Schema):
    containers = fields.List(fields.Dict(keys=fields.String(), values=fields.Raw()), required=True)


class ProxmoxGuestsQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    agent_id = fields.Integer(required=True)


class ProxmoxGuestSchema(Schema):
    vmid = fields.Integer(required=True)
    name = fields.String(allow_none=True)
    node = fields.String(required=True)
    status = fields.String(allow_none=True)
    type = fields.String(required=True, validate=validate.OneOf(_PROXMOX_GUEST_TYPES))


class ProxmoxGuestsResponseSchema(Schema):
    guests = fields.List(fields.Nested(ProxmoxGuestSchema), required=True)


class JobStatusResponseSchema(Schema):
    id = fields.Integer(required=True)
    state = fields.Method("get_state")
    started = fields.DateTime(allow_none=True)
    ended = fields.DateTime(allow_none=True)
    log = fields.String(allow_none=True)
    data = fields.Dict(keys=fields.String(), values=fields.Raw(), allow_none=True)

    def get_state(self, obj):
        return obj.state.name if obj.state else None
