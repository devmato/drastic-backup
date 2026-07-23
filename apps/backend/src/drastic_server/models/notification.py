from flask import current_app
from sqlalchemy.ext.mutable import MutableList

from drastic_server.extensions import db
from drastic_server.models.agent import AgentOperationState, AgentOperationType
from drastic_server.models.mixins import IdMixin, TimeMixin
from drastic_server.utils.crypto import CryptoError, decrypt, encrypt


class NotificationConfig(IdMixin, TimeMixin, db.Model):
    __tablename__ = 'notification_configs'
    _url = db.Column("encrypted_url", db.JSON, nullable=False)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('notification_configs') )
    operation_types = db.Column(MutableList.as_mutable(db.JSON), nullable=False, default=list)
    operation_states = db.Column(MutableList.as_mutable(db.JSON), nullable=False, default=list)

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


class NotificationDelivery(IdMixin, TimeMixin, db.Model):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        db.UniqueConstraint(
            "operation_id",
            "state",
            "notification_config_id",
            name="uq_notification_deliveries_operation_state_config",
        ),
        db.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        db.Index("ix_notification_deliveries_open", "sent_at", "created", "id"),
    )

    operation_id = db.Column(
        db.Integer,
        db.ForeignKey("agent_operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation = db.relationship(
        "AgentOperation",
        foreign_keys=[operation_id],
        backref=db.backref("notification_deliveries", cascade="all, delete-orphan"),
    )
    state = db.Column(db.String(255), nullable=False)
    notification_config_id = db.Column(
        db.Integer,
        db.ForeignKey("notification_configs.id", ondelete="CASCADE"),
        nullable=False,
    )
    notification_config = db.relationship(
        "NotificationConfig",
        foreign_keys=[notification_config_id],
        backref=db.backref("deliveries", cascade="all, delete-orphan"),
    )
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
