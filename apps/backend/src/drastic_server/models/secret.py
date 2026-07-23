import enum

from sqlalchemy.ext.mutable import MutableDict

from drastic_server.extensions import db
from drastic_server.models.mixins import IdMixin, TimeMixin


class UserSecretType(str, enum.Enum):
    repository_password = "repository_password"


class UserSecret(IdMixin, TimeMixin, db.Model):
    __tablename__ = "user_secrets"
    __table_args__ = (
        db.UniqueConstraint("user_id", "type", "name", name="uq_user_secrets_user_id_type_name"),
    )

    TYPE_REPOSITORY_PASSWORD = UserSecretType.repository_password.value

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("secrets"))
    type = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    encrypted_value = db.Column(db.JSON, nullable=False)
    public_data = db.Column(MutableDict.as_mutable(db.JSON), nullable=False, default=dict)
    version = db.Column(db.Integer, nullable=False, default=1)


class AgentSecretEnvelope(IdMixin, TimeMixin, db.Model):
    __tablename__ = "agent_secret_envelopes"
    __table_args__ = (
        db.UniqueConstraint(
            "user_secret_id",
            "agent_id",
            name="uq_agent_secret_envelopes_secret_id_agent_id",
        ),
    )

    user_secret_id = db.Column(
        db.Integer,
        db.ForeignKey("user_secrets.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_secret = db.relationship(
        "UserSecret",
        foreign_keys=[user_secret_id],
        backref=db.backref("agent_envelopes", cascade="all, delete-orphan"),
    )
    agent_id = db.Column(
        db.Integer,
        db.ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent = db.relationship(
        "Agent",
        foreign_keys=[agent_id],
        backref=db.backref("secret_envelopes", cascade="all, delete-orphan"),
    )
    encrypted_value = db.Column(db.JSON, nullable=False)
    secret_version = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
