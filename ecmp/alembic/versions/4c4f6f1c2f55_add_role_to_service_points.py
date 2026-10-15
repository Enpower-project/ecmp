"""add_role_to_service_points

Revision ID: 4c4f6f1c2f55
Revises: 02314ef496e5
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "4c4f6f1c2f55"
down_revision: Union[str, Sequence[str], None] = "02314ef496e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


service_point_role = postgresql.ENUM(
    "producer",
    "consumer",
    "prosumer",
    name="service_point_role",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    service_point_role.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "service_points",
        sa.Column("role", service_point_role, nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("service_points", "role")
    service_point_role.drop(op.get_bind(), checkfirst=True)
