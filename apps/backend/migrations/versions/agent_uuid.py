"""Give agents a durable identity independent of installation-local IDs."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "agent_uuid"
down_revision = "truenas_paths"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("uuid", sa.String(36), nullable=True))
    agents = sa.table("agents", sa.column("id", sa.Integer()), sa.column("uuid", sa.String(36)))
    connection = op.get_bind()
    for agent_id in connection.execute(sa.select(agents.c.id)).scalars().all():
        connection.execute(agents.update().where(agents.c.id == agent_id).values(uuid=str(uuid4())))
    with op.batch_alter_table("agents") as batch:
        batch.alter_column("uuid", existing_type=sa.String(36), nullable=False)
        batch.create_unique_constraint("uq_agents_uuid", ["uuid"])


def downgrade():
    with op.batch_alter_table("agents") as batch:
        batch.drop_constraint("uq_agents_uuid", type_="unique")
        batch.drop_column("uuid")
