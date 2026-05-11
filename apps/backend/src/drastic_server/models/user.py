import bcrypt
import secrets

from drastic_common.secret_envelope import encrypt_with_password

from drastic_server.extensions import db
from drastic_server.models.mixins import IdMixin, TimeMixin


class User(IdMixin, TimeMixin, db.Model):
    __tablename__ = 'users'
    name = db.Column(db.String(120), unique=True)
    email = db.Column(db.String(120), unique=True)
    password = db.Column(db.String(120), nullable=True)
    encrypted_recovery_key = db.Column(db.JSON, nullable=True)
    sessions = db.relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

    # Set Password method
    def set_password(self, password, recovery_key=None):
        self.password = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        recovery_key = recovery_key or secrets.token_urlsafe(32)
        self.encrypted_recovery_key = encrypt_with_password(recovery_key, password)
