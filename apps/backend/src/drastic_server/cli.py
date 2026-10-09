import getpass

import bcrypt
import click
from flask import Flask, current_app
from flask.cli import AppGroup
from sqlalchemy.exc import IntegrityError

from drastic_server.extensions import db
from drastic_server.models.agent import Agent, AgentSession
from drastic_server.models.user import User
from drastic_server.services.operations.lifecycle import (
    reconcile_unknown_agent_operation_dispatches,
)
from drastic_server.services.operations.reports import (
    STARTUP_NOTIFICATION_RETRY_LIMIT,
    AgentOperationService,
)
from drastic_server.services.repository import reconcile_native_repository_quarantine

user_cli = AppGroup("user")
agent_cli = AppGroup("agent")
seed_cli = AppGroup("seed")
maintenance_cli = AppGroup("maintenance")
repository_cli = AppGroup("repository")


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
    if user is not None:
        click.echo(f"Skipping bootstrap admin seed because user '{username}' already exists")
        return user

    user = User(name=username)
    db.session.add(user)
    user.set_initial_password(password)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        click.echo("Skipping bootstrap admin seed because the user could not be persisted")
        return None

    click.echo(f"Created bootstrap admin user '{username}'")
    return user


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
    user.set_initial_password(password)

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
    _seed_bootstrap_admin_if_configured()


@maintenance_cli.command("startup")
def startup_maintenance():
    deleted_sessions = AgentSession.query.delete()
    db.session.commit()
    reconciled_operations = reconcile_unknown_agent_operation_dispatches()
    restored, deleted = reconcile_native_repository_quarantine()
    retried_deliveries = AgentOperationService._retry_open_notification_deliveries(
        limit=STARTUP_NOTIFICATION_RETRY_LIMIT
    )
    click.echo(
        f"Startup maintenance complete: {deleted_sessions} sessions removed, "
        f"{reconciled_operations} unknown dispatches reconciled, "
        f"{restored} repositories restored, {deleted} quarantines removed, "
        f"{retried_deliveries} notification deliveries retried"
    )


@repository_cli.command("reconcile-quarantine")
def repository_reconcile_quarantine():
    restored, deleted = reconcile_native_repository_quarantine()
    click.echo(f"Reconciled repository quarantine: {restored} restored, {deleted} removed")


def register_cli(app: Flask) -> None:
    app.cli.add_command(user_cli)
    app.cli.add_command(agent_cli)
    app.cli.add_command(seed_cli)
    app.cli.add_command(maintenance_cli)
    app.cli.add_command(repository_cli)
