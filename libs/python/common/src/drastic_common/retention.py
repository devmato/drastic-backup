"""Validated retention rules shared by the API and the deleting agent."""

from datetime import date

from marshmallow import Schema, ValidationError, fields, validate, validates_schema


class RetentionRuleSchema(Schema):
    type = fields.String(required=True, validate=validate.OneOf(("last", "recent", "since", "calendar")))
    count = fields.Integer(strict=True, validate=validate.Range(min=1, max=2147483647))
    period = fields.String(validate=validate.OneOf(("hour", "day", "week", "month", "year")))
    days = fields.Integer(strict=True, validate=validate.Range(min=0, max=365000))
    weeks = fields.Integer(strict=True, validate=validate.Range(min=0, max=52000))
    months = fields.Integer(strict=True, validate=validate.Range(min=0, max=12000))
    date = fields.String(validate=validate.Regexp(r"^\d{4}-\d{2}-\d{2}$"))
    weekday = fields.Integer(strict=True, validate=validate.Range(min=0, max=6))
    day = fields.Integer(strict=True, validate=validate.Range(min=1, max=31))
    month = fields.Integer(strict=True, validate=validate.Range(min=1, max=12))

    @validates_schema
    def validate_rule(self, data, **kwargs):
        kind = data["type"]
        required = {"last": {"count"}, "recent": set(), "since": {"date"},
                    "calendar": {"count", "period"}}[kind]
        allowed = {"last": {"count", "period"}, "recent": {"days", "weeks", "months"},
                   "since": {"date"}, "calendar": {"count", "period"}}[kind]
        if kind == "calendar":
            if data.get("period") == "hour":
                raise ValidationError({"period": ["Hourly rules use last backups, at most one per hour."]})
            selector = {"week": "weekday", "month": "day", "year": "month"}.get(data.get("period"))
            if selector:
                required.add(selector)
                allowed.add(selector)
        missing = required - data.keys()
        extra = data.keys() - allowed - {"type"}
        if missing or extra:
            raise ValidationError({**{key: ["Required"] for key in missing},
                                   **{key: ["Not applicable to this rule"] for key in extra}})
        if kind == "recent" and not any(data.get(key, 0) for key in ("days", "weeks", "months")):
            raise ValidationError("Specify a positive retention period")
        if kind == "since":
            try:
                date.fromisoformat(data["date"])
            except ValueError as exc:
                raise ValidationError({"date": ["Invalid date"]}) from exc


class RetentionPolicySchema(Schema):
    rules = fields.List(fields.Nested(RetentionRuleSchema), required=True,
                        validate=validate.Length(min=1, max=100))
