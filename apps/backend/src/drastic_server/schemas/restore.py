from marshmallow import EXCLUDE, Schema, fields, validate


class RestoreOptionsQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    job_id = fields.Integer(required=True)


class RestoreModeSchema(Schema):
    value = fields.String(required=True)
    label = fields.String(required=True)
    description = fields.String(required=True)


class RestoreOptionsResponseSchema(Schema):
    job_id = fields.Integer(required=True)
    job_uuid = fields.String(required=True)
    modes = fields.List(fields.Nested(RestoreModeSchema), required=True)


class RestoreSnapshotsQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    job_id = fields.Integer(required=True)
    agent_id = fields.Integer(required=True)
    repository_id = fields.Integer(required=True)


class RestoreSnapshotSchema(Schema):
    id = fields.String(required=True)
    short_id = fields.String(allow_none=True)
    time = fields.Raw(allow_none=True)
    paths = fields.List(fields.String(), load_default=list)
    tags = fields.List(fields.String(), load_default=list)
    hostname = fields.String(allow_none=True)


class RestoreSnapshotsResponseSchema(Schema):
    snapshots = fields.List(fields.Dict(keys=fields.String(), values=fields.Raw()), required=True)


class RestoreEntriesQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    agent_id = fields.Integer(required=True)
    repository_id = fields.Integer(required=True)
    snapshot_id = fields.String(required=True, validate=validate.Length(min=1))
    path = fields.String(load_default="/")


class RestoreEntriesResponseSchema(Schema):
    entries = fields.List(fields.Dict(keys=fields.String(), values=fields.Raw()), required=True)


class RestoreStartInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    job_id = fields.Integer(required=True)
    agent_id = fields.Integer(required=True)
    repository_id = fields.Integer(required=True)
    mode = fields.String(required=True, validate=validate.OneOf(("plain_file",)))
    snapshot_id = fields.String(required=True, validate=validate.Length(min=1))
    restore_location = fields.String(required=True, validate=validate.Length(min=1))
    include_paths = fields.List(
        fields.String(validate=validate.Length(min=1)),
        required=True,
        validate=validate.Length(min=1),
    )


class RestoreStartResponseSchema(Schema):
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
