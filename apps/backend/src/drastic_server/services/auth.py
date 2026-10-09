from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import bcrypt
from flask_jwt_extended import create_access_token, create_refresh_token, decode_token

from drastic_server.extensions import db
from drastic_server.models.user import User
from drastic_server.models.user_session import UserSession
from drastic_server.services.exceptions import AuthenticationFailed
from drastic_server.services.repository import RepositorySecretError, ensure_user_recovery_key


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SessionAuthService:
    """Session lifecycle; callers pass request metadata and set response cookies."""

    @classmethod
    def login(cls, username, password, *, user_agent=""):
        user = User.query.filter_by(name=username.lower()).first()
        if not user or not bcrypt.checkpw(password.encode("UTF-8"), user.password.encode("UTF-8")):
            raise AuthenticationFailed("Wrong username or password")
        try:
            recovery_key = ensure_user_recovery_key(user, password)
        except RepositorySecretError as exc:
            raise AuthenticationFailed("Wrong username or password") from exc
        _, access, refresh = cls.create_session(user, user_agent=user_agent)
        db.session.commit()
        return recovery_key, access, refresh

    @classmethod
    def refresh(cls, public_id, token, *, user_agent=""):
        session = cls.get_active_session_by_public_id(public_id)
        if not cls.verify_refresh_token(session, token):
            raise AuthenticationFailed("Invalid session")
        tokens = cls.rotate_refresh_token(session, user_agent=user_agent)
        db.session.commit()
        return tokens

    @classmethod
    def logout(cls, public_id):
        cls.revoke_session_by_public_id(public_id)
        db.session.commit()

    @classmethod
    def create_session(cls, user: User, *, user_agent: str = "") -> tuple[UserSession, str, str]:
        session = UserSession(
            user=user,
            refresh_token_hash="",
            expires_at=utcnow(),
            last_seen_at=utcnow(),
            user_agent=user_agent.strip() or None,
        )
        db.session.add(session)
        db.session.flush()

        access_token, refresh_token, refresh_expires_at = cls._issue_tokens(user, session)
        session.refresh_token_hash = cls.hash_token(refresh_token)
        session.expires_at = refresh_expires_at
        return session, access_token, refresh_token

    @classmethod
    def rotate_refresh_token(cls, session: UserSession, *, user_agent: str = "") -> tuple[str, str]:
        access_token, refresh_token, refresh_expires_at = cls._issue_tokens(session.user, session)
        session.refresh_token_hash = cls.hash_token(refresh_token)
        session.expires_at = refresh_expires_at
        session.last_seen_at = utcnow()
        session.user_agent = user_agent.strip() or session.user_agent
        return access_token, refresh_token

    @classmethod
    def revoke_session(cls, session: UserSession | None) -> None:
        if session is None or session.revoked_at is not None:
            return
        session.revoked_at = utcnow()

    @classmethod
    def revoke_session_by_public_id(cls, session_public_id: str | None) -> None:
        cls.revoke_session(cls.get_session_by_public_id(session_public_id))

    @classmethod
    def get_session_by_public_id(cls, session_public_id: str | None) -> UserSession | None:
        if not session_public_id:
            return None
        return UserSession.query.filter_by(public_id=str(session_public_id)).first()

    @classmethod
    def get_active_session_by_public_id(cls, session_public_id: str | None) -> UserSession | None:
        session = cls.get_session_by_public_id(session_public_id)
        if session is None or not session.active:
            return None
        return session

    @classmethod
    def verify_refresh_token(cls, session: UserSession | None, token: str | None) -> bool:
        if session is None or not token:
            return False
        return hmac.compare_digest(session.refresh_token_hash, cls.hash_token(token))

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def decode_session_public_id(
        cls, encoded_token: str | None, *, allow_expired: bool = False
    ) -> str | None:
        if not encoded_token:
            return None
        try:
            payload = decode_token(encoded_token, allow_expired=allow_expired)
        except Exception:
            return None
        return str(payload.get("sid") or "").strip() or None

    @classmethod
    def _issue_tokens(cls, user: User, session: UserSession) -> tuple[str, str, datetime]:
        additional_claims = {"sid": str(session.public_id)}
        access_token = create_access_token(identity=str(user.id), additional_claims=additional_claims)
        refresh_token = create_refresh_token(identity=str(user.id), additional_claims=additional_claims)
        refresh_payload = decode_token(refresh_token)
        refresh_expires_at = datetime.fromtimestamp(refresh_payload["exp"], UTC).replace(tzinfo=None)
        return access_token, refresh_token, refresh_expires_at
