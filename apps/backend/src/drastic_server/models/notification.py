from flask import current_app

from drastic_server.extensions import db
from drastic_server.models.agent import AgentOperationState, AgentOperationType
from drastic_server.models.mixins import IdMixin, TimeMixin
from drastic_server.utils.crypto import CryptoError, decrypt, encrypt


class NotificationConfig(IdMixin, TimeMixin, db.Model):
    __tablename__ = 'notification_configs'
    _url = db.Column(db.JSON, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('notification_configs') )
    operation_types = db.Column(db.JSON, nullable=False, default=[])
    operation_states = db.Column(db.JSON, nullable=False, default=[])

    @property
    def url(self):
        try:
            return decrypt(**self._url, key=current_app.config['ENCRYPTION_KEY'])
        except (CryptoError, TypeError):
            return ''

    @url.setter
    def url(self, value):
        self._url = encrypt(plaintext=value, key=current_app.config['ENCRYPTION_KEY'])

    @property
    def operation_type_enums(self):
        operation_type_enums = []

        for operation_type in self.operation_types:
            operation_type_enums.append( AgentOperationType[operation_type] )

        return operation_type_enums
    

    @property
    def operation_state_enums(self):
        operation_state_enums = []

        for operation_state in self.operation_states:
            operation_state_enums.append( AgentOperationState[operation_state] )

        return operation_state_enums
