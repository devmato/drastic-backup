"""Retention policy API inputs and presentation."""

from marshmallow import Schema, fields, validate

from drastic_common.retention import RetentionPolicySchema, RetentionRuleSchema


class RetentionResponseSchema(Schema):
    id = fields.Integer(required=True)
    name = fields.String(required=True)
    rules = fields.List(fields.Nested(RetentionRuleSchema), required=True)
    created = fields.DateTime(allow_none=True)
    is_default = fields.Function(lambda obj: obj.user.default_retention_id == obj.id)


class RetentionCreateInputSchema(RetentionPolicySchema):
    name = fields.String(required=True, validate=validate.Length(min=1, max=255))


class RetentionUpdateInputSchema(Schema):
    is_default = fields.Boolean()
    name = fields.String(validate=validate.Length(min=1, max=255))
    rules = fields.List(fields.Nested(RetentionRuleSchema), validate=validate.Length(min=1, max=100))
