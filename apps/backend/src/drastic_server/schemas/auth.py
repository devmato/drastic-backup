from marshmallow import EXCLUDE, Schema, fields, validate

from drastic_server.schemas.user import UserResponseSchema


class LoginInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    username = fields.String(required=True, validate=validate.Length(min=1))
    password = fields.String(required=True, validate=validate.Length(min=1))


class LoginResponseSchema(Schema):
    ok = fields.Boolean(required=True)
    recovery_key = fields.String(allow_none=True)


class AuthStatusResponseSchema(Schema):
    authenticated = fields.Boolean(required=True)
    user = fields.Nested(UserResponseSchema, allow_none=True)
    environment = fields.String(required=True)
