from drastic_server.extensions import db
from drastic_server.models.mixins import IdMixin, TimeMixin


# Rentention policy
class Retention(IdMixin, TimeMixin, db.Model):
    __tablename__ = 'retentions'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('retentions') )
    name = db.Column(db.String(255), nullable=False)
    keep_last = db.Column(db.Integer, nullable=True)
    keep_hourly = db.Column(db.Integer, nullable=True)
    keep_weekly = db.Column(db.Integer, nullable=True)
    keep_monthly = db.Column(db.Integer, nullable=True)
    keep_yearly = db.Column(db.Integer, nullable=True)

    @property
    def rtype(self):
        if self.keep_last:
            return 'count'
        return 'date'