import secrets

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
    name = db.Column(db.String(120), unique=True)
    email = db.Column(db.String(120), unique=True)
    password = db.Column(db.String(120), nullable=True)
    encrypted_recovery_key = db.Column(db.JSON, nullable=True)
    sessions = db.relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

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
