"""Give each account a stable namespace for directly tagged repository backups."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "backup_owner"
down_revision = "job_retention"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("backup_owner_uuid", sa.String(36), nullable=True))
    connection = op.get_bind()
    for (user_id,) in connection.execute(sa.text("SELECT id FROM users")):
        connection.execute(sa.text("UPDATE users SET backup_owner_uuid = :owner WHERE id = :id"),
                           {"owner": str(uuid4()), "id": user_id})
    with op.batch_alter_table("users") as batch:
        batch.alter_column("backup_owner_uuid", existing_type=sa.String(36), nullable=False)
        batch.create_unique_constraint("uq_users_backup_owner_uuid", ["backup_owner_uuid"])


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_backup_owner_uuid", type_="unique")
        batch.drop_column("backup_owner_uuid")
