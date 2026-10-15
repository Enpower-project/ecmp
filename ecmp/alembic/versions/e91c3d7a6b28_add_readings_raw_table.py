"""add readings_raw table

Revision ID: e91c3d7a6b28
Revises: 7a8e8c1d2b44
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e91c3d7a6b28"
down_revision: Union[str, Sequence[str], None] = "7a8e8c1d2b44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "readings_raw",
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column(
            "flags",
            postgresql.ARRAY(
                postgresql.ENUM(
                    "V_VALID",
                    "V_SUSPECT",
                    "V_REJECTED",
                    "E_ESTIMATED",
                    "E_EDITED",
                    "SRC_PRIMARY",
                    "SRC_BACKFILL",
                    name="quality_flag",
                    create_type=False,
                )
            ),
            nullable=False,
            server_default=sa.text("ARRAY['SRC_PRIMARY','V_VALID']::quality_flag[]"),
        ),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.channel_id"]),
        sa.PrimaryKeyConstraint("channel_id", "ts"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("readings_raw")
