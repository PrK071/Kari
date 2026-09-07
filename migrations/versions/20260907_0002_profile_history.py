"""Persist reading history per profile.

Revision ID: 20260907_0002
Revises: 20260903_0001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260907_0002"
down_revision = "20260903_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("item_key", sa.String(length=2048), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "item_key", name="uq_history_profile_item"),
    )


def downgrade() -> None:
    op.drop_table("profile_history")
