from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates_schema

from drastic_server.schemas.job import ScheduleConfigSchema
from drastic_server.schemas.scheduling import ScheduleTriggerSchema


class ChainStepInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    job_id = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))
    repository_id = fields.Integer(required=True, strict=True, validate=validate.Range(min=1))
    retention_id = fields.Integer(load_default=None, allow_none=True, strict=True, validate=validate.Range(min=1))
    config = fields.Nested(ScheduleConfigSchema, load_default=dict)


class ChainInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    schedules = fields.List(fields.Nested(ScheduleTriggerSchema), required=True, validate=validate.Length(max=100))
    recovery_key = fields.String(load_default=None, allow_none=True)
    start_timeout_minutes = fields.Integer(load_default=60, strict=True, validate=validate.Range(min=1, max=1440))
    steps = fields.List(fields.Nested(ChainStepInputSchema), required=True, validate=validate.Length(min=1, max=100))

    @validates_schema
    def validate_steps(self, data, **kwargs):
        ids = [step["job_id"] for step in data.get("steps", [])]
        if len(ids) != len(set(ids)):
            raise ValidationError({"steps": ["A job can occur only once in a chain"]})
        if not data.get("name", "").strip():
            raise ValidationError({"name": ["Enter a chain name"]})
