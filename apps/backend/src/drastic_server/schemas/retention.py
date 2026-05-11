from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates_schema

_RETENTION_TYPES = ("count", "date")


class RetentionSchema(Schema):
    id = fields.Integer(required=True)
    name = fields.String(required=True)
    keep_last = fields.Integer(allow_none=True)
    keep_hourly = fields.Integer(allow_none=True)
    keep_weekly = fields.Integer(allow_none=True)
    keep_monthly = fields.Integer(allow_none=True)
    keep_yearly = fields.Integer(allow_none=True)


class RetentionResponseSchema(Schema):
    id = fields.Integer(required=True)
    name = fields.String(required=True)
    rtype = fields.String(attribute="rtype", required=True)
    keep_last = fields.Integer(allow_none=True)
    keep_hourly = fields.Integer(allow_none=True)
    keep_weekly = fields.Integer(allow_none=True)
    keep_monthly = fields.Integer(allow_none=True)
    keep_yearly = fields.Integer(allow_none=True)
    created = fields.DateTime(allow_none=True)


class RetentionCreateInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    name = fields.String(required=True, validate=validate.Length(min=1))
    rtype = fields.String(load_default="count", validate=validate.OneOf(_RETENTION_TYPES))
    keep_last = fields.Integer(load_default=1, validate=validate.Range(min=0))
    keep_hourly = fields.Integer(load_default=0, validate=validate.Range(min=0))
    keep_weekly = fields.Integer(load_default=0, validate=validate.Range(min=0))
    keep_monthly = fields.Integer(load_default=0, validate=validate.Range(min=0))
    keep_yearly = fields.Integer(load_default=0, validate=validate.Range(min=0))

    @validates_schema
    def validate_shape(self, data, **kwargs):
        if data["rtype"] == "count":
            if data["keep_last"] < 1:
                raise ValidationError(
                    {"keep_last": ["keep_last must be at least 1 for count retentions."]}
                )

            invalid_fields = [
                field_name
                for field_name in ("keep_hourly", "keep_weekly", "keep_monthly", "keep_yearly")
                if data.get(field_name)
            ]
            if invalid_fields:
                raise ValidationError(
                    {
                        field_name: ["Date-based keep_* fields must be 0 for count retentions."]
                        for field_name in invalid_fields
                    }
                )
            return

        if data.get("keep_last"):
            raise ValidationError({"keep_last": ["keep_last must be 0 for date retentions."]})

        if not any(
            data.get(field_name, 0) > 0
            for field_name in ("keep_hourly", "keep_weekly", "keep_monthly", "keep_yearly")
        ):
            raise ValidationError(
                {
                    "keep_hourly": ["At least one date-based keep_* field must be greater than 0."],
                }
            )


class RetentionUpdateInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    name = fields.String(validate=validate.Length(min=1))
    rtype = fields.String(validate=validate.OneOf(_RETENTION_TYPES))
    keep_last = fields.Integer(validate=validate.Range(min=0))
    keep_hourly = fields.Integer(validate=validate.Range(min=0))
    keep_weekly = fields.Integer(validate=validate.Range(min=0))
    keep_monthly = fields.Integer(validate=validate.Range(min=0))
    keep_yearly = fields.Integer(validate=validate.Range(min=0))
