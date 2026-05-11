import secrets

import bcrypt

from drastic_common.secret_envelope import (
    SecretEnvelopeError,
    decrypt_with_password,
    encrypt_for_public_key,
    encrypt_with_password,
)
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentRepositorySecret
from drastic_server.models.repository import Repository
from drastic_server.models.user import User


class RepositorySecretError(ValueError):
    pass


def create_user_recovery_key() -> str:
    return secrets.token_urlsafe(32)


def ensure_user_recovery_key(user: User, account_password: str) -> str:
    if not user.encrypted_recovery_key:
        recovery_key = create_user_recovery_key()
        user.encrypted_recovery_key = encrypt_with_password(recovery_key, account_password)
        return recovery_key

    try:
        return decrypt_with_password(user.encrypted_recovery_key, account_password)
    except SecretEnvelopeError as exc:
        raise RepositorySecretError("Wrong account password") from exc


def reveal_repository_recovery_key(
    user: User,
    repository: Repository,
    account_password: str,
) -> str:
    if not account_password or not user.password:
        raise RepositorySecretError("Account password is required")
    if not bcrypt.checkpw(account_password.encode("UTF-8"), user.password.encode("UTF-8")):
        raise RepositorySecretError("Wrong account password")

    user_recovery_key = ensure_user_recovery_key(user, account_password)
    return decrypt_recovery_key(repository, user_recovery_key)


def validate_user_recovery_key(recovery_key: str | None) -> str:
    normalized = str(recovery_key or "").strip()
    if not normalized:
        raise RepositorySecretError("Recovery key is required. Sign in again to unlock secret operations.")
    return normalized


def store_recovery_key(repository: Repository, repository_recovery_key: str, user_recovery_key: str) -> None:
    repository.encrypted_recovery_key = encrypt_with_password(
        repository_recovery_key, validate_user_recovery_key(user_recovery_key)
    )


def decrypt_recovery_key(repository: Repository, user_recovery_key: str) -> str:
    if not repository.encrypted_recovery_key:
        raise RepositorySecretError("Repository has no encrypted recovery key")
    try:
        return decrypt_with_password(
            repository.encrypted_recovery_key, validate_user_recovery_key(user_recovery_key)
        )
    except SecretEnvelopeError as exc:
        raise RepositorySecretError("Wrong recovery key. Sign in again to unlock secret operations.") from exc


def ensure_agent_recovery_envelope(agent: Agent, repository: Repository, recovery_key: str) -> None:
    if not agent.public_key:
        return

    existing = AgentRepositorySecret.query.filter(
        AgentRepositorySecret.agent_id == agent.id,
        AgentRepositorySecret.repository_id == repository.id,
    ).first()
    envelope = encrypt_for_public_key(recovery_key, agent.public_key)
    if existing:
        existing.encrypted_recovery_key = envelope
        existing.encrypted_agent_key = None
        existing.provisioned = False
        return

    db.session.add(
        AgentRepositorySecret(
            agent_id=agent.id,
            repository_id=repository.id,
            encrypted_recovery_key=envelope,
            provisioned=False,
        )
    )


def ensure_user_agent_recovery_envelopes(user_id: int, repository: Repository, recovery_key: str) -> None:
    for agent in Agent.query.filter(Agent.user_id == user_id).all():
        ensure_agent_recovery_envelope(agent, repository, recovery_key)


def ensure_agent_envelopes_from_user_recovery_key(
    agent: Agent,
    repositories: list[Repository],
    user_recovery_key: str | None,
) -> None:
    missing = [
        repository
        for repository in repositories
        if repository.encrypted_recovery_key
        and agent.public_key
        and not AgentRepositorySecret.query.filter(
            AgentRepositorySecret.agent_id == agent.id,
            AgentRepositorySecret.repository_id == repository.id,
        ).first()
    ]
    if not missing:
        return

    user_recovery_key = validate_user_recovery_key(user_recovery_key)
    for repository in missing:
        recovery_key = decrypt_recovery_key(repository, user_recovery_key)
        ensure_agent_recovery_envelope(agent, repository, recovery_key)
