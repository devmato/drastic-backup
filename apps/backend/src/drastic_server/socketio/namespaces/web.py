from flask import current_app, request
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request
from flask_socketio import Namespace, join_room

from drastic_server.extensions import db, socketio
from drastic_server.models.user import User
from drastic_server.models.user_session import UserSession
from drastic_server.utils.realtime import session_room, user_room


class WebNamespace(Namespace):
    def on_connect(self, auth):
        try:
            verify_jwt_in_request(locations=["cookies"])
            user = db.session.get(User, int(get_jwt_identity()))
            # Hold revocation until the room is joined, so logout cannot miss a connecting tab.
            session = UserSession.query.filter_by(
                public_id=get_jwt().get("sid"), user_id=get_jwt_identity(),
            ).populate_existing().with_for_update().first()
            if not user or not session or not session.active:
                db.session.rollback()
                return False
            join_room(user_room(user.id))
            join_room(session_room(session.public_id))
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.info(f"Rejecting WebSocket client {request.sid}: invalid session")
            return False

        current_app.logger.debug(f"WebSocket client {request.sid} joined user room {user.id}")


webnamespace = WebNamespace("/")
socketio.on_namespace(webnamespace)
