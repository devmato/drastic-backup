import secrets
from uuid import uuid4

import bcrypt

from drastic_common.secret_envelope import (
    SecretEnvelopeError,
    decrypt_with_password,
    encrypt_with_password,
)
from drastic_server.extensions import db
from drastic_server.models.mixins import IdMixin, TimeMixin


class User(IdMixin, TimeMixin, db.Model):
    __tablename__ = 'users'
    name = db.Column(db.String(120), nullable=False, unique=True)
    email = db.Column(db.String(120), unique=True)
    schedule_timezone = db.Column(db.String(255), nullable=True)
    backup_owner_uuid = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    default_retention_id = db.Column(
        db.Integer,
        db.ForeignKey("retentions.id", ondelete="SET NULL", name="fk_users_default_retention", use_alter=True),
        nullable=True,
    )
    password = db.Column(db.String(120), nullable=False)
    encrypted_recovery_key = db.Column(db.JSON, nullable=False)
    debug_token_hash = db.Column(db.String(64), nullable=True, unique=True)
    debug_enabled_at = db.Column(db.DateTime, nullable=True)
    sessions = db.relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def effective_schedule_timezone(self):
        return self.schedule_timezone or "UTC"

    def check_password(self, password: str) -> bool:
        if not self.password:
            return False
        return bcrypt.checkpw(password.encode(), self.password.encode())

    def set_initial_password(self, password: str, recovery_key: str | None = None) -> None:
        if self.password or self.encrypted_recovery_key:
            raise ValueError("Initial password is already set")
        self.password = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        recovery_key = recovery_key or secrets.token_urlsafe(32)
        self.encrypted_recovery_key = encrypt_with_password(recovery_key, password)

    def change_password(self, current_password: str, new_password: str) -> None:
        if not self.check_password(current_password):
            raise ValueError("Wrong password")
        if not self.encrypted_recovery_key:
            raise ValueError("Recovery key is missing")

        try:
            recovery_key = decrypt_with_password(self.encrypted_recovery_key, current_password)
        except SecretEnvelopeError as exc:
            raise ValueError("Wrong password") from exc

        self.password = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        self.encrypted_recovery_key = encrypt_with_password(recovery_key, new_password)
