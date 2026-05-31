"""refactor repository secrets and add agent ssh identity

Revision ID: secret_refactor
Revises: init
Create Date: 2026-05-15 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "secret_refactor"
down_revision = "init"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_secrets",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("encrypted_value", sa.JSON(), nullable=False),
        sa.Column("public_data", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_secrets_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_secrets")),
        sa.UniqueConstraint("user_id", "type", "name", name="uq_user_secrets_user_id_type_name"),
    )
    op.create_table(
        "agent_secret_envelopes",
        sa.Column("user_secret_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_value", sa.JSON(), nullable=False),
        sa.Column("secret_version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_secret_envelopes_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["user_secret_id"],
            ["user_secrets.id"],
            name=op.f("fk_agent_secret_envelopes_user_secret_id_user_secrets"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_secret_envelopes")),
        sa.UniqueConstraint(
            "user_secret_id", "agent_id", name="uq_agent_secret_envelopes_secret_id_agent_id"
        ),
    )

    op.add_column("agents", sa.Column("ssh_public_key", sa.Text(), nullable=True))
    op.add_column("agents", sa.Column("ssh_key_fingerprint", sa.String(length=255), nullable=True))
    op.add_column("agents", sa.Column("ssh_key_algorithm", sa.String(length=64), nullable=True))

    op.add_column("repositories", sa.Column("password_secret_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_repositories_password_secret_id_user_secrets"),
        "repositories",
        "user_secrets",
        ["password_secret_id"],
        ["id"],
    )

    op.add_column(
        "agent_repositories", sa.Column("encrypted_restic_access_key", sa.JSON(), nullable=True)
    )
    op.add_column("agent_repositories", sa.Column("restic_key_id", sa.String(length=255), nullable=True))
    op.add_column(
        "agent_repositories", sa.Column("provisioned", sa.Boolean(), nullable=False, server_default=sa.false())
    )

    op.alter_column("agent_repositories", "provisioned", server_default=None)
    op.drop_table("agent_repository_secrets")
    op.drop_column("repositories", "encrypted_recovery_key")


def downgrade():
    op.add_column("repositories", sa.Column("encrypted_recovery_key", sa.JSON(), nullable=True))
    op.create_table(
        "agent_repository_secrets",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_recovery_key", sa.JSON(), nullable=False),
        sa.Column("encrypted_agent_key", sa.JSON(), nullable=True),
        sa.Column("restic_key_id", sa.String(length=255), nullable=True),
        sa.Column("provisioned", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_agent_repository_secrets_agent_id_agents")),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], name=op.f("fk_agent_repository_secrets_repository_id_repositories")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_repository_secrets")),
        sa.UniqueConstraint("agent_id", "repository_id", name="uq_agent_repository_secrets_agent_id_repository_id"),
    )
    op.drop_column("agent_repositories", "provisioned")
    op.drop_column("agent_repositories", "restic_key_id")
    op.drop_column("agent_repositories", "encrypted_restic_access_key")
    op.drop_constraint(op.f("fk_repositories_password_secret_id_user_secrets"), "repositories", type_="foreignkey")
    op.drop_column("repositories", "password_secret_id")
    op.drop_column("agents", "ssh_key_algorithm")
    op.drop_column("agents", "ssh_key_fingerprint")
    op.drop_column("agents", "ssh_public_key")
    op.drop_table("agent_secret_envelopes")
    op.drop_table("user_secrets")
