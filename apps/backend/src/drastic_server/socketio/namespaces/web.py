from flask import current_app, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from flask_socketio import Namespace, join_room

from drastic_server.extensions import socketio
from drastic_server.models.user import User
from drastic_server.utils.realtime import user_room


class WebNamespace(Namespace):
    def on_connect(self, auth):
        try:
            verify_jwt_in_request(locations=["cookies"])
            user = User.query.get(int(get_jwt_identity()))
        except Exception:
            current_app.logger.info(f"Rejecting WebSocket client {request.sid}: invalid session")
            return False

        if not user:
            current_app.logger.info(f"Rejecting WebSocket client {request.sid}: unknown user")
            return False

        join_room(user_room(user.id))
        current_app.logger.debug(f"WebSocket client {request.sid} joined user room {user.id}")


webnamespace = WebNamespace("/")
socketio.on_namespace(webnamespace)
