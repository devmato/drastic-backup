"""Account initialization, password changes and recovery-export assembly."""

from drastic_server.extensions import db
from drastic_server.integrations.agent import AgentService
from drastic_server.models.agent import Agent
from drastic_server.models.user import PasswordTooLong, User
from drastic_server.services.auth import SessionAuthService
from drastic_server.services.exceptions import AuthenticationFailed, InvalidInput
from drastic_server.services.queries import require_result
from drastic_server.services.recovery_export import (
    RecoveryExportError,
    RecoveryExportPasswordError,
    build_recovery_archive,
    build_recovery_export_payload,
)
from drastic_server.utils.realtime import disconnect_session_sockets


def get_user(user_id):
    return require_result(User.query.filter_by(id=int(user_id)))


def update_settings(user_id, data):
    """Commit the account zone before notifying agents; offline agents pick it up on sync."""
    user = get_user(user_id)
    user.schedule_timezone = data["schedule_timezone"]
    db.session.commit()
    for agent in Agent.query.filter_by(user_id=user.id):
        if agent.online:
            AgentService.sync(agent)
    return user


def needs_initialization():
    return User.query.count() == 0


def initialize_user(data):
    if not needs_initialization():
        raise ValueError("Initial user already exists")
    user = User(name=data["username"].strip().lower())
    try:
        user.set_initial_password(data["password"])
    except PasswordTooLong as exc:
        raise InvalidInput(str(exc)) from exc
    db.session.add(user)
    db.session.commit()


def change_password(user_id, data):
    """Rewrap the account recovery key and revoke browser/debug access in the same commit."""
    user = get_user(user_id)
    try:
        user.change_password(data["current_password"], data["new_password"])
    except PasswordTooLong as exc:
        raise InvalidInput(str(exc)) from exc
    except ValueError as exc:
        raise AuthenticationFailed("Wrong password") from exc
    # Credential changes invalidate browser sessions and independent debug access together.
    sessions = list(user.sessions)
    for session in sessions:
        SessionAuthService.revoke_session(session)
    user.debug_token_hash = None
    user.debug_enabled_at = None
    db.session.commit()
    for session in sessions:
        disconnect_session_sockets(session.public_id)


def recovery_archive(user_id, password, archive_password, restic_targets):
    """Reauthenticate before assembling an encrypted kit; neither password is persisted."""
    user = get_user(user_id)
    try:
        payload = build_recovery_export_payload(user, password)
    except RecoveryExportPasswordError as exc:
        raise AuthenticationFailed("Wrong password") from exc
    except RecoveryExportError as exc:
        raise InvalidInput("Recovery export could not decrypt all required data") from exc
    return build_recovery_archive(payload, archive_password, restic_targets)
