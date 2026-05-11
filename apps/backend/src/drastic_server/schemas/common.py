from marshmallow import Schema, fields


class MessageSchema(Schema):
    msg = fields.String(required=True)


class MessageIdSchema(MessageSchema):
    id = fields.Integer(required=True)
