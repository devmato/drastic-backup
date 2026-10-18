"""Use one explicit scheduling time zone per account."""

import sqlalchemy as sa
from alembic import op

revision = "user_timezone"
down_revision = "backup_owner"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("schedule_timezone", sa.String(255), nullable=False, server_default="UTC"))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("schedule_timezone")
