from marshmallow import EXCLUDE, Schema, fields, validate


class UserResponseSchema(Schema):
    id = fields.Integer(required=True)
    name = fields.String(required=True)
    email = fields.Email(allow_none=True)


class UserInitInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    username = fields.String(required=True, validate=validate.Length(min=1))
    password = fields.String(required=True, validate=validate.Length(min=8))


class UserChangePasswordInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    current_password = fields.String(required=True, validate=validate.Length(min=1))
    new_password = fields.String(required=True, validate=validate.Length(min=8))


class UserRecoveryExportInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    password = fields.String(required=True, validate=validate.Length(min=1))


class NeedsInitResponseSchema(Schema):
    needs_init = fields.Boolean(required=True)
