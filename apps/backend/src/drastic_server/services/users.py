"""Account initialization, password changes and recovery-export assembly."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from flask import render_template

from drastic_server.extensions import db
from drastic_server.models.user import User
from drastic_server.services.auth import SessionAuthService
from drastic_server.services.exceptions import AuthenticationFailed, InvalidInput
from drastic_server.services.queries import require_result
from drastic_server.services.recovery_export import (
    RecoveryExportError,
    RecoveryExportPasswordError,
    build_recovery_export_payload,
)


def get_user(user_id):
    return require_result(User.query.filter_by(id=int(user_id)))


def needs_initialization():
    return User.query.count() == 0


def initialize_user(data):
    if not needs_initialization():
        raise ValueError("Initial user already exists")
    user = User(name=data["username"].strip().lower())
    user.set_initial_password(data["password"])
    db.session.add(user)
    db.session.commit()


def change_password(user_id, data):
    user = get_user(user_id)
    try:
        user.change_password(data["current_password"], data["new_password"])
    except ValueError as exc:
        raise AuthenticationFailed("Wrong password") from exc
    # Credential changes invalidate browser sessions and independent debug access together.
    for session in list(user.sessions):
        SessionAuthService.revoke_session(session)
    user.debug_token_hash = None
    user.debug_enabled_at = None
    db.session.commit()


def recovery_archive(user_id, password):
    user = get_user(user_id)
    try:
        payload = build_recovery_export_payload(user, password)
    except RecoveryExportPasswordError as exc:
        raise AuthenticationFailed("Wrong password") from exc
    except RecoveryExportError as exc:
        raise InvalidInput("Recovery export could not decrypt all required data") from exc
    html = render_template("recovery/recovery.html", export=payload)
    archive = BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as recovery_zip:
        recovery_zip.writestr("recovery.html", html)
    archive.seek(0)
    return archive
