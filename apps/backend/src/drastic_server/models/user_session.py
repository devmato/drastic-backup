from datetime import datetime, timezone
from uuid import uuid4

from drastic_server.extensions import db
from drastic_server.models.mixins import IdMixin, TimeMixin


class UserSession(IdMixin, TimeMixin, db.Model):
    __tablename__ = "user_sessions"

    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    refresh_token_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    last_seen_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)

    user = db.relationship("User", back_populates="sessions")

    @property
    def active(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return self.revoked_at is None and self.expires_at > now
