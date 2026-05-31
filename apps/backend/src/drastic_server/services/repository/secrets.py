import secrets

import bcrypt
from sqlalchemy import select, update

from drastic_common.secret_envelope import (
    SecretEnvelopeError,
    decrypt_with_password,
    encrypt_for_public_key,
    encrypt_with_password,
)
from drastic_server.extensions import db
from drastic_server.models.agent import Agent, agent_repositories
from drastic_server.models.repository import Repository
from drastic_server.models.secret import AgentSecretEnvelope, UserSecret
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
    secret = repository.password_secret
    if secret is None:
        secret = UserSecret(
            user_id=repository.user_id,
            type=UserSecret.TYPE_REPOSITORY_PASSWORD,
            name=f"{repository.name} repository password",
            public_data={},
            version=0,
        )
        repository.password_secret = secret

    secret.encrypted_value = encrypt_with_password(
        repository_recovery_key, validate_user_recovery_key(user_recovery_key)
    )
    secret.version = (secret.version or 0) + 1


def decrypt_recovery_key(repository: Repository, user_recovery_key: str) -> str:
    if not repository.password_secret or not repository.password_secret.encrypted_value:
        raise RepositorySecretError("Repository has no encrypted recovery key")
    try:
        return decrypt_with_password(
            repository.password_secret.encrypted_value, validate_user_recovery_key(user_recovery_key)
        )
    except SecretEnvelopeError as exc:
        raise RepositorySecretError("Wrong recovery key. Sign in again to unlock secret operations.") from exc


def decrypt_user_secret(secret: UserSecret, user_recovery_key: str) -> str:
    try:
        return decrypt_with_password(secret.encrypted_value, validate_user_recovery_key(user_recovery_key))
    except SecretEnvelopeError as exc:
        raise RepositorySecretError("Wrong recovery key. Sign in again to unlock secret operations.") from exc


def ensure_agent_recovery_envelope(agent: Agent, repository: Repository, recovery_key: str) -> None:
    if not agent.public_key or not repository.password_secret:
        return

    ensure_agent_secret_envelope(agent, repository.password_secret, recovery_key)


def ensure_agent_secret_envelope(agent: Agent, secret: UserSecret, plaintext: str) -> None:
    if not agent.public_key:
        return

    existing = AgentSecretEnvelope.query.filter(
        AgentSecretEnvelope.agent_id == agent.id,
        AgentSecretEnvelope.user_secret_id == secret.id,
    ).first()
    envelope = encrypt_for_public_key(plaintext, agent.public_key)
    if existing:
        existing.encrypted_value = envelope
        existing.secret_version = secret.version
        existing.active = True
        return

    db.session.add(
        AgentSecretEnvelope(
            agent_id=agent.id,
            user_secret_id=secret.id,
            encrypted_value=envelope,
            secret_version=secret.version,
            active=True,
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
    secrets_by_id = {}
    for repository in repositories:
        secret = repository.password_secret
        if not secret or not agent.public_key:
            continue
        existing = AgentSecretEnvelope.query.filter(
            AgentSecretEnvelope.agent_id == agent.id,
            AgentSecretEnvelope.user_secret_id == secret.id,
            AgentSecretEnvelope.secret_version == secret.version,
            AgentSecretEnvelope.active.is_(True),
        ).first()
        if not existing:
            secrets_by_id[secret.id] = secret

    if not secrets_by_id:
        return

    user_recovery_key = validate_user_recovery_key(user_recovery_key)
    for secret in secrets_by_id.values():
        ensure_agent_secret_envelope(agent, secret, decrypt_user_secret(secret, user_recovery_key))


def repository_password_envelope(agent: Agent, repository: Repository) -> AgentSecretEnvelope | None:
    if not repository.password_secret_id:
        return None
    return AgentSecretEnvelope.query.filter(
        AgentSecretEnvelope.agent_id == agent.id,
        AgentSecretEnvelope.user_secret_id == repository.password_secret_id,
        AgentSecretEnvelope.active.is_(True),
    ).first()


def store_agent_restic_access_key(
    agent: Agent,
    repository_id: int,
    encrypted_restic_access_key,
    restic_key_id: str | None = None,
) -> None:
    db.session.execute(
        update(agent_repositories)
        .where(
            agent_repositories.c.agent_id == agent.id,
            agent_repositories.c.repository_id == repository_id,
        )
        .values(
            encrypted_restic_access_key=encrypted_restic_access_key,
            restic_key_id=restic_key_id,
            provisioned=True,
        )
    )


def agent_repository_assignment(agent: Agent, repository_id: int):
    return db.session.execute(
        select(agent_repositories).where(
            agent_repositories.c.agent_id == agent.id,
            agent_repositories.c.repository_id == repository_id,
        )
    ).mappings().first()
