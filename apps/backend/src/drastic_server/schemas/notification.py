from marshmallow import EXCLUDE, Schema, fields, validate

from drastic_server.models.agent import AgentOperationState, AgentOperationType

_OPERATION_TYPE_NAMES = tuple(AgentOperationType.__members__)
_OPERATION_STATE_NAMES = tuple(AgentOperationState.__members__)


class NotificationConfigResponseSchema(Schema):
    id = fields.Integer(required=True)
    url = fields.String(required=True)
    operation_types = fields.List(fields.String(), required=True)
    operation_states = fields.List(fields.String(), required=True)
    operation_type_labels = fields.Method("get_operation_type_labels")
    operation_state_labels = fields.Method("get_operation_state_labels")
    created = fields.DateTime(allow_none=True)

    def get_operation_type_labels(self, obj):
        return [
            AgentOperationType[operation_type].value["text"]
            for operation_type in obj.operation_types
            if operation_type in AgentOperationType.__members__
        ]

    def get_operation_state_labels(self, obj):
        return [operation_state.capitalize() for operation_state in obj.operation_states]


class NotificationCreateInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    url = fields.String(required=True, validate=validate.Length(min=1))
    operation_types = fields.List(
        fields.String(validate=validate.OneOf(_OPERATION_TYPE_NAMES)),
        required=True,
        validate=validate.Length(min=1),
    )
    operation_states = fields.List(
        fields.String(validate=validate.OneOf(_OPERATION_STATE_NAMES)),
        required=True,
        validate=validate.Length(min=1),
    )


class NotificationUpdateInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    url = fields.String(validate=validate.Length(min=1))
    operation_types = fields.List(
        fields.String(validate=validate.OneOf(_OPERATION_TYPE_NAMES)),
        validate=validate.Length(min=1),
    )
    operation_states = fields.List(
        fields.String(validate=validate.OneOf(_OPERATION_STATE_NAMES)),
        validate=validate.Length(min=1),
    )


class NotificationTestInputSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    url = fields.String(required=True, validate=validate.Length(min=1))


class NotificationTestResponseSchema(Schema):
    msg = fields.String(required=True)
    success = fields.Boolean(required=True)


class NotificationOptionItemSchema(Schema):
    value = fields.String(required=True)
    label = fields.String(required=True)


class NotificationOptionsResponseSchema(Schema):
    operation_types = fields.List(fields.Nested(NotificationOptionItemSchema), required=True)
    operation_states = fields.List(fields.Nested(NotificationOptionItemSchema), required=True)
