"""Owner-scoped repository management and recoverable native deletion."""

import logging

from sqlalchemy.exc import IntegrityError

from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentOperation, AgentOperationState, AgentSession
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.services.chains import require_unused_chain_reference
from drastic_server.services.exceptions import AuthenticationFailed, InvalidInput, ResourceConflict
from drastic_server.services.queries import require_result
from drastic_server.services.repository import (
    RepositorySecretError,
    delete_quarantined_native_repository,
    ensure_user_agent_recovery_envelopes,
    generate_native_repository_password,
    generate_native_repository_path,
    quarantine_native_repository,
    restore_quarantined_native_repository,
    reveal_repository_recovery_key,
    store_recovery_key,
    validate_user_recovery_key,
)


def get_repository(user_id, repository_id):
    return require_result(Repository.query.filter_by(id=repository_id, user_id=user_id))


def list_repositories(user_id):
    return Repository.query.filter_by(user_id=user_id).all()


def create_repository(user_id, data):
    if not data.get("name"):
        raise InvalidInput("Repository name is required")
    recovery_key = validate_user_recovery_key(data.get("recovery_key"))
    if Repository.query.filter_by(user_id=user_id, name=data["name"]).first():
        raise ResourceConflict("Repository with this name already exists")
    kind = data.get("kind") or Repository.KIND_CUSTOM
    environment = data.get("environment") or {}
    location = data.get("location")
    password = data.get("password") or generate_native_repository_password()
    if kind == Repository.KIND_NATIVE:
        if data.get("location"):
            raise InvalidInput("location is not allowed for native repositories")
        if data.get("repository_path"):
            raise InvalidInput("repository_path is managed by the server for native repositories")
        location = generate_native_repository_path()
        existing = Repository.query.filter_by(user_id=user_id, kind=Repository.KIND_NATIVE).all()
        if any(candidate.repository_path == location for candidate in existing):
            raise ResourceConflict("Native repository path already exists")
        environment = {}
    elif not location:
        raise InvalidInput("location is required for custom repositories")

    repository = Repository(name=data["name"], kind=kind, location=location,
                            environment=environment, restic_id=None, user_id=user_id)
    store_recovery_key(repository, password, recovery_key)
    db.session.add(repository)
    try:
        db.session.flush()
        ensure_user_agent_recovery_envelopes(int(user_id), repository, password)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ResourceConflict("Repository with this name already exists") from exc
    return repository


def update_repository(user_id, repository_id, data):
    repository = get_repository(user_id, repository_id)
    if data.get("name"):
        repository.name = data["name"]
    if repository.kind == Repository.KIND_CUSTOM:
        if data.get("location"):
            repository.location = data["location"]
        if "environment" in data:
            repository.environment = data.get("environment") or {}
    if data.get("password"):
        raise ValueError("Repository password rotation is not supported yet")
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ResourceConflict("Repository with this name already exists") from exc


def reveal_password(user_id, repository_id, password):
    user = require_result(User.query.filter_by(id=int(user_id)))
    repository = get_repository(user_id, repository_id)
    try:
        return reveal_repository_recovery_key(user=user, repository=repository, account_password=password)
    except RepositorySecretError as exc:
        raise AuthenticationFailed(str(exc)) from exc


def delete_repository(user_id, repository_id):
    """Quarantine files first, restore on rollback, purge only after the commit."""
    repository = get_repository(user_id, repository_id)
    try:
        require_unused_chain_reference(user_id, "repository_id", repository.id)
    except ValueError as exc:
        raise ResourceConflict(str(exc)) from exc
    if repository.schedules:
        raise ValueError("Can't remove repository used by schedules. Remove those schedules first.")
    quarantine = None
    try:
        if repository.kind == Repository.KIND_NATIVE:
            running = AgentOperation.query.filter_by(repository_id=repository.id, state=AgentOperationState.running).first()
            if running is not None:
                raise ResourceConflict("Repository has a running operation")
            if repository.repository_path:
                quarantine = quarantine_native_repository(repository.repository_path)
        password_secret = repository.password_secret
        repository.agents = []
        db.session.delete(repository)
        db.session.flush()
        if password_secret:
            db.session.delete(password_secret)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if quarantine is not None:
            restore_quarantined_native_repository(quarantine)
        if isinstance(exc, RuntimeError):
            raise ResourceConflict(str(exc)) from exc
        raise
    if quarantine is not None:
        try:
            delete_quarantined_native_repository(quarantine)
        except (OSError, RuntimeError) as exc:
            logging.getLogger(__name__).error("Could not purge quarantined native repository (%s)", type(exc).__name__)


def unlock_agents(user_id, repository_id):
    get_repository(user_id, repository_id)
    agents = Agent.query.join(AgentSession).filter(
        Agent.user_id == user_id, Agent.repositories.any(Repository.id == repository_id),
    ).all()
    return [agent for agent in agents if agent.online]
