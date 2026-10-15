"""add_service_point_allocation_tables

Revision ID: 7a8e8c1d2b44
Revises: 5d70c7f42d31
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "7a8e8c1d2b44"
down_revision: Union[str, Sequence[str], None] = "5d70c7f42d31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "service_point_allocation_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ec_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("producer_sp_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_sp_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.CheckConstraint(
            "producer_sp_id <> recipient_sp_id",
            name="ck_service_point_allocation_link_distinct_points",
        ),
        sa.ForeignKeyConstraint(["ec_id"], ["energy_communities.ec_id"]),
        sa.ForeignKeyConstraint(["producer_sp_id"], ["service_points.sp_id"]),
        sa.ForeignKeyConstraint(["recipient_sp_id"], ["service_points.sp_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ec_id",
            "producer_sp_id",
            "recipient_sp_id",
            name="uq_service_point_allocation_link_triplet",
        ),
    )
    op.create_table(
        "service_point_allocation_coefficients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("allocation_link_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("valid_from", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("valid_to", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("coefficient", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.CheckConstraint(
            "coefficient >= 0 AND coefficient <= 1",
            name="ck_service_point_allocation_coefficients_range",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_service_point_allocation_coefficients_window",
        ),
        sa.ForeignKeyConstraint(
            ["allocation_link_id"],
            ["service_point_allocation_links.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "allocation_link_id",
            "valid_from",
            name="uq_service_point_allocation_coefficients_link_valid_from",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("service_point_allocation_coefficients")
    op.drop_table("service_point_allocation_links")
