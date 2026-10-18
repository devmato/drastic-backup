from marshmallow import EXCLUDE, Schema, fields, validate

from drastic_common.restic.binary import RECOVERY_TARGETS
from drastic_common.scheduling import validate_timezone


class UserResponseSchema(Schema):
    id = fields.Integer(required=True)
    name = fields.String(required=True)
    email = fields.Email(allow_none=True)
    debug_enabled = fields.Function(lambda user: bool(user.debug_token_hash))
    schedule_timezone = fields.String(required=True, allow_none=True)


class UserSettingsInputSchema(Schema):
    schedule_timezone = fields.String(required=True, validate=[validate.Length(max=255), validate_timezone])


class TimezonesResponseSchema(Schema):
    timezones = fields.List(fields.String(), required=True)


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
    archive_password = fields.String(required=True, validate=validate.Regexp(r"^[A-Za-z0-9_-]{32}$"))
    restic_targets = fields.List(
        fields.String(validate=validate.OneOf(RECOVERY_TARGETS)),
        load_default=list,
        validate=validate.Length(max=len(RECOVERY_TARGETS)),
    )


class NeedsInitResponseSchema(Schema):
    needs_init = fields.Boolean(required=True)
