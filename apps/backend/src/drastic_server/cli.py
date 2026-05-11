import getpass

import bcrypt
import click
from flask import Flask, current_app
from flask.cli import AppGroup
from sqlalchemy.exc import IntegrityError

from drastic_server.extensions import db
from drastic_server.models.agent import Agent
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.services.repository import (
    ensure_native_repository_initialized,
    ensure_user_recovery_key,
    generate_native_repository_password,
    store_recovery_key,
)
from drastic_server.utils.urls import normalize_restic_repository_path

user_cli = AppGroup("user")
agent_cli = AppGroup("agent")
seed_cli = AppGroup("seed")


def _configured_bootstrap_admin() -> tuple[str, str]:
    username = str(current_app.config.get("BOOTSTRAP_ADMIN_USERNAME") or "").strip().lower()
    password = str(current_app.config.get("BOOTSTRAP_ADMIN_PASSWORD") or "")
    if username and password:
        return username, password

    if str(current_app.config.get("DRASTIC_ENV") or "").strip().lower() == "dev":
        legacy_username = (
            str(current_app.config.get("DEV_SEED_ADMIN_USERNAME") or "").strip().lower()
        )
        legacy_password = str(current_app.config.get("DEV_SEED_ADMIN_PASSWORD") or "")
        if legacy_username and legacy_password:
            return legacy_username, legacy_password

    return "", ""


def _seed_bootstrap_admin_if_configured() -> User | None:
    username, password = _configured_bootstrap_admin()
    if not username or not password:
        click.echo("Skipping bootstrap admin seed because credentials are not configured")
        return None

    user = User.query.filter_by(name=username).first()
    created = user is None
    if user is not None:
        update_existing = _config_bool("BOOTSTRAP_ADMIN_UPDATE")
        if not update_existing:
            click.echo(
                f"Skipping bootstrap admin seed because user '{username}' already exists"
            )
            return user
    else:
        user = User(name=username)
        db.session.add(user)

    user.set_password(password)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        click.echo("Skipping bootstrap admin seed because the user could not be persisted")
        return None

    click.echo(f"{'Created' if created else 'Updated'} bootstrap admin user '{username}'")
    return user


def _config_bool(name: str) -> bool:
    value = current_app.config.get(name)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _seed_bootstrap_repository_if_configured(user: User | None) -> bool:
    if user is None:
        click.echo("Skipping bootstrap repository seed because no bootstrap admin is configured")
        return False

    name = str(current_app.config.get("BOOTSTRAP_REPOSITORY_NAME") or "").strip()
    location = str(current_app.config.get("BOOTSTRAP_REPOSITORY_LOCATION") or "").strip()
    repository_path = str(current_app.config.get("BOOTSTRAP_REPOSITORY_PATH") or "").strip()
    password = str(current_app.config.get("BOOTSTRAP_REPOSITORY_PASSWORD") or "")
    if not name or ((not repository_path and not location) or (not password and not repository_path)):
        click.echo("Skipping bootstrap repository seed because repository settings are incomplete")
        return False

    normalized_path = None
    kind = Repository.KIND_CUSTOM
    if not location and repository_path:
        try:
            normalized_path = normalize_restic_repository_path(repository_path)
        except ValueError as exc:
            click.echo(f"Skipping bootstrap repository seed because the path is invalid: {exc}")
            return False

        location = normalized_path
        kind = Repository.KIND_NATIVE

    repository = None
    for candidate in Repository.query.filter_by(user_id=user.id).all():
        candidate_path = candidate.repository_path
        if (
            (normalized_path and candidate_path == normalized_path)
            or candidate.location == location
            or candidate.name == name
        ):
            repository = candidate
            break

    created = repository is None
    if repository is None:
        repository = Repository(user_id=user.id)
        db.session.add(repository)

    repository.name = name
    repository.kind = kind
    repository.location = location
    repository.environment = {}

    _, account_password = _configured_bootstrap_admin()
    if not account_password:
        click.echo("Skipping bootstrap repository seed because admin password is unavailable")
        return False
    user_recovery_key = ensure_user_recovery_key(user, account_password)

    if kind == Repository.KIND_NATIVE and normalized_path:
        try:
            if not password:
                password = generate_native_repository_password()

            config = ensure_native_repository_initialized(
                repository_path=normalized_path,
                password=password,
            )
            repository.restic_id = str(config.get("id") or "")[:8] or None
        except Exception as exc:
            click.echo(f"Skipping bootstrap repository seed because initialization failed: {exc}")
            db.session.rollback()
            return False

    store_recovery_key(repository, password, user_recovery_key)

    db.session.commit()

    click.echo(f"{'Created' if created else 'Updated'} bootstrap repository '{name}'")
    return True


@user_cli.command("create")
@click.argument("name")
def user_create(name):
    print(f"Creating user: {name}")

    password_prompt = True
    while password_prompt:
        password = getpass.getpass("Password: ")
        passwort_repeat = getpass.getpass("Confirm password: ")

        if password == passwort_repeat:
            password_prompt = False
        else:
            print("Passwords dont match. Please repeat!")

    user = User(name=name)
    user.set_password(password)

    try:
        db.session.add(user)
        db.session.commit()
    except IntegrityError:
        print(f"Error: A user with the name {name} already exists")
        return

    print("User created successfully!")


@agent_cli.command("create")
@click.argument("username")
def agent_create(username):
    print("Creating agent")

    user = User.query.filter(User.name == username).first()

    if not user:
        print(f"User {username} doesnt exist! Canceling...")
        return

    secret = Agent.generate_secret()
    secret_hash = bcrypt.hashpw(secret.encode(), bcrypt.gensalt())

    agent = Agent(secret=secret_hash.decode(), user_id=user.id)

    db.session.add(agent)
    db.session.commit()

    print("Agent created:")
    print(f"ID: {agent.id}, Secret: {secret}")


@seed_cli.command("dev")
def seed_dev():
    seed_bootstrap()


@seed_cli.command("bootstrap")
def seed_bootstrap():
    user = _seed_bootstrap_admin_if_configured()
    _seed_bootstrap_repository_if_configured(user)


def register_cli(app: Flask) -> None:
    app.cli.add_command(user_cli)
    app.cli.add_command(agent_cli)
    app.cli.add_command(seed_cli)
