import posixpath

from marshmallow import EXCLUDE, Schema, ValidationError, fields, post_load, validate


def normalize_restore_target(value):
    if "\x00" in value:
        raise ValidationError("Restore location must not contain NUL bytes")
    if not value.startswith("/"):
        raise ValidationError("Restore location must be an absolute POSIX path")
    if ".." in value.split("/"):
        raise ValidationError("Restore location must not contain traversal segments")
    normalized = posixpath.normpath(value)
    if normalized == "/":
        raise ValidationError("Restoring to / is not allowed")
    return normalized


def normalize_include_path(value):
    if "\x00" in value:
        raise ValidationError("Include paths must not contain NUL bytes")
    if ".." in value.split("/"):
        raise ValidationError("Include paths must not contain traversal segments")
    return posixpath.normpath("/" + value.lstrip("/"))


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
    overwrite_policy = fields.String(
        load_default="fail_if_exists",
        validate=validate.OneOf(("fail_if_exists", "overwrite")),
    )

    @post_load
    def normalize_paths(self, data, **kwargs):
        data["restore_location"] = normalize_restore_target(data["restore_location"])
        data["include_paths"] = list(
            dict.fromkeys(normalize_include_path(path) for path in data["include_paths"])
        )
        return data


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
    dispatch_status = fields.String(required=True)
