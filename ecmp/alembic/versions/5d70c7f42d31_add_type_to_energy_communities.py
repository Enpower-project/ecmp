"""add_type_to_energy_communities

Revision ID: 5d70c7f42d31
Revises: 4c4f6f1c2f55
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "5d70c7f42d31"
down_revision: Union[str, Sequence[str], None] = "4c4f6f1c2f55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


community_type = postgresql.ENUM(
    "CSC_00",
    name="community_type",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    community_type.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "energy_communities",
        sa.Column("type", community_type, nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("energy_communities", "type")
    community_type.drop(op.get_bind(), checkfirst=True)
