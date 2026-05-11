from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from flask import request
from flask_jwt_extended import create_access_token, create_refresh_token, decode_token

from drastic_server.extensions import db
from drastic_server.models.user import User
from drastic_server.models.user_session import UserSession


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SessionAuthService:
    @classmethod
    def create_session(cls, user: User) -> tuple[UserSession, str, str]:
        session = UserSession(
            user=user,
            refresh_token_hash="",
            expires_at=utcnow(),
            last_seen_at=utcnow(),
            user_agent=str(request.user_agent.string or "").strip() or None,
        )
        db.session.add(session)
        db.session.flush()

        access_token, refresh_token, refresh_expires_at = cls._issue_tokens(user, session)
        session.refresh_token_hash = cls.hash_token(refresh_token)
        session.expires_at = refresh_expires_at
        return session, access_token, refresh_token

    @classmethod
    def rotate_refresh_token(cls, session: UserSession) -> tuple[str, str]:
        access_token, refresh_token, refresh_expires_at = cls._issue_tokens(session.user, session)
        session.refresh_token_hash = cls.hash_token(refresh_token)
        session.expires_at = refresh_expires_at
        session.last_seen_at = utcnow()
        session.user_agent = str(request.user_agent.string or "").strip() or session.user_agent
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
