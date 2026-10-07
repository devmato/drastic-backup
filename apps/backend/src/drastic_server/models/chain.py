from drastic_server.extensions import db
from drastic_server.models.mixins import IdMixin, TimeMixin


class BackupChain(IdMixin, TimeMixin, db.Model):
    __tablename__ = "backup_chains"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    schedules = db.Column(db.JSON, nullable=False, default=list)
    start_timeout_minutes = db.Column(db.Integer, nullable=False, default=60)
    steps = db.Column(db.JSON, nullable=False, default=list)

    @property
    def enabled(self):
        return any(schedule["enabled"] for schedule in self.schedules or [])


class BackupChainRun(IdMixin, TimeMixin, db.Model):
    __tablename__ = "backup_chain_runs"
    __table_args__ = (db.UniqueConstraint("chain_id", "planned_slot", name="uq_chain_run_slot"),)
    chain_id = db.Column(db.Integer, db.ForeignKey("backup_chains.id", ondelete="CASCADE"), nullable=False, index=True)
    chain = db.relationship("BackupChain", backref=db.backref("runs", cascade="all, delete-orphan"))
    # Nullable unique key: one active run, including concurrent manual starts.
    active_chain_id = db.Column(db.Integer, unique=True, nullable=True)
    planned_slot = db.Column(db.DateTime, nullable=True)
    state = db.Column(db.String(32), nullable=False, default="running")
    steps = db.Column(db.JSON, nullable=False)
    start_timeout_minutes = db.Column(db.Integer, nullable=False)
    started = db.Column(db.DateTime, nullable=False)
    ended = db.Column(db.DateTime, nullable=True)
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    lease_until = db.Column(db.DateTime, nullable=True)
    lease_token = db.Column(db.String(36), nullable=True)
