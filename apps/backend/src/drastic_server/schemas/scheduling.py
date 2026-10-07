from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates_schema

from drastic_common.scheduling import TimingSchema


class ScheduleTriggerSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    enabled = fields.Boolean(required=True)
    timing = fields.Nested(TimingSchema)
    # Existing clients can still submit the original weekly form.
    minute = fields.String(validate=validate.Regexp(r"^(?:[0-5]?\d)$"))
    hour = fields.String(validate=validate.Regexp(r"^(?:[01]?\d|2[0-3])$"))
    day_of_week = fields.List(fields.Integer(strict=True, validate=validate.Range(min=0, max=6)), validate=validate.Length(min=1, max=7))

    @validates_schema
    def validate_trigger(self, data, **kwargs):
        if "timing" in data:
            return
        missing = [key for key in ("minute", "hour", "day_of_week") if key not in data]
        if missing:
            raise ValidationError({key: ["Required"] for key in missing})
        if len(set(data["day_of_week"])) != len(data["day_of_week"]):
            raise ValidationError({"day_of_week": ["Duplicate weekdays are not allowed."]})


class SchedulePreviewSchema(Schema):
    timing = fields.Nested(TimingSchema, required=True)
    agent_id = fields.Integer(load_default=None, allow_none=True, strict=True)
