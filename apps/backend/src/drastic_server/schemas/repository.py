from marshmallow import EXCLUDE, Schema, fields, validate

from drastic_server.models.repository import Repository, RepositoryKind

_REPOSITORY_KIND_NAMES = tuple(kind.value for kind in RepositoryKind)


class RepositorySchema(Schema):
    id = fields.Integer(required=True)
    kind = fields.String(required=True, validate=validate.OneOf(_REPOSITORY_KIND_NAMES))
    location = fields.String(required=True, validate=validate.Length(min=1))
    environment = fields.Dict(
        keys=fields.String(),
        values=fields.Raw(),
        load_default=dict,
        dump_default=dict,
    )


class RepositoryResponseSchema(Schema):
    id = fields.Integer(required=True)
    name = fields.String(required=True)
    kind = fields.String(required=True)
    location = fields.Method("get_location")
    repository_path = fields.Method("get_repository_path", dump_only=True)
    environment = fields.Dict(keys=fields.String(), values=fields.Raw(), dump_default=dict)
    restic_id = fields.String(allow_none=True)
    stats = fields.Raw(allow_none=True)
    created = fields.DateTime(allow_none=True)

    def get_location(self, obj):
        return obj.public_location

    def get_repository_path(self, obj):
        return obj.repository_path


class RepositoryManageSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    name = fields.String(validate=validate.Length(min=1))
    kind = fields.String(
        validate=validate.OneOf(_REPOSITORY_KIND_NAMES),
    )
    password = fields.String(load_default=None, allow_none=True, validate=validate.Length(min=1))
    recovery_key = fields.String(load_default=None, allow_none=True, validate=validate.Length(min=1))
    location = fields.String(load_default=None, allow_none=True, validate=validate.Length(min=1))
    repository_path = fields.String(
        load_default=None, allow_none=True, validate=validate.Length(min=1)
    )
    environment = fields.Dict(keys=fields.String(), values=fields.Raw())


class RepositoryRevealPasswordInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    account_password = fields.String(required=True, validate=validate.Length(min=1))


class RepositoryRevealPasswordResponseSchema(Schema):
    password = fields.String(required=True)


class UnlockInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    agent_id = fields.Integer(required=True)


class CheckInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    agent_id = fields.Integer(required=True)
    read_data_subset = fields.String(load_default=None, allow_none=True)


class UnlockResponseSchema(Schema):
    msg = fields.String(required=True)
    success = fields.Boolean(required=True)


class UnlockAgentResponseSchema(Schema):
    id = fields.Integer(required=True)
    hostname = fields.String(allow_none=True)
    online = fields.Boolean(required=True)
