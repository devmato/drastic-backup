from drastic_server.extensions import db


class DiagnosticEvent(db.Model):
    __tablename__ = "diagnostic_events"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True)
    event_uuid = db.Column(db.String(36), nullable=False)
    operation_uuid = db.Column(db.String(36), nullable=True)
    component = db.Column(db.String(32), nullable=False)
    event_type = db.Column(db.String(64), nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False)
    received_at = db.Column(db.DateTime, nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    __table_args__ = (
        db.UniqueConstraint("user_id", "event_uuid", name="uq_diagnostic_event_uuid"),
        db.Index("ix_diagnostics_owner_id", "user_id", "id"),
        db.Index("ix_diagnostics_operation", "user_id", "operation_uuid", "id"),
        db.Index("ix_diagnostics_received", "received_at"),
    )
