import enum
import json

from flask import current_app

from drastic_server.extensions import db
from drastic_server.models.agent import AgentOperationType
from drastic_server.models.mixins import IdMixin, TimeMixin
from drastic_server.utils.crypto import CryptoError, decrypt, encrypt


class RepositoryKind(str, enum.Enum):
    custom = "custom"
    native = "native"


class Repository(IdMixin, TimeMixin, db.Model):
    __tablename__ = "repositories"
    __table_args__ = (db.UniqueConstraint("user_id", "name", name="uq_repositories_user_id_name"),)
    KIND_CUSTOM = RepositoryKind.custom.value
    KIND_NATIVE = RepositoryKind.native.value

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("repositories"))
    name = db.Column(db.String(255), nullable=False)
    kind = db.Column(db.String(32), nullable=False, default=KIND_CUSTOM)
    location = db.Column(db.String(255), nullable=False)
    encrypted_recovery_key = db.Column(db.JSON, nullable=True)
    restic_id = db.Column(db.String(255), nullable=True)
    # agent_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=False)
    # agent = db.relationship('Agent', foreign_keys=[agent_id], backref=db.backref('repositories') )
    # environment = db.Column(db.JSON, nullable=False, default=[])
    _environment = db.Column(db.JSON, nullable=True)

    @property
    def environment(self):
        if self._environment:
            try:
                return json.loads(
                    decrypt(
                        **self._environment,
                        key=current_app.config["ENCRYPTION_KEY"],
                    )
                )
            except (CryptoError, TypeError, KeyError, json.JSONDecodeError):
                pass

        return {}

    @environment.setter
    def environment(self, value):
        self._environment = encrypt(
            plaintext=json.dumps(value), key=current_app.config["ENCRYPTION_KEY"]
        )

    @property
    def stats(self):
        for operation in reversed(self.operations):
            if operation.type == AgentOperationType.repository_stats and "stats" in operation.data:
                return operation.data["stats"]

    @property
    def repository_path(self):
        from drastic_server.utils.urls import (
            extract_managed_restic_repository_path,
            normalize_restic_repository_path,
        )

        if self.kind != self.KIND_NATIVE:
            return None

        raw_location = str(self.location or "").strip()
        if not raw_location:
            return None

        if raw_location.startswith("rest:"):
            return extract_managed_restic_repository_path(raw_location)

        try:
            return normalize_restic_repository_path(raw_location)
        except ValueError:
            return None

    @property
    def public_location(self):
        from drastic_server.utils.urls import build_managed_restic_location

        if self.kind == self.KIND_NATIVE:
            repository_path = self.repository_path
            if repository_path is None:
                return None
            return build_managed_restic_location(current_app.config, repository_path)

        return self.location
