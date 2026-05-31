from datetime import datetime

from drastic_server.extensions import db


# Id column
class IdMixin():
    id = db.Column(db.Integer, primary_key=True)

# Created and updated columns
class TimeMixin():
    created = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated = db.Column(db.DateTime, onupdate=datetime.now)
