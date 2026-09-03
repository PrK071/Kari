"""Create identity and profile schema.

Revision ID: 20260903_0001
Revises:
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260903_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=48), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=False),
        sa.Column("background_url", sa.Text(), nullable=False),
        sa.Column("home_background_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("login_key", sa.String(length=160), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("password_salt", sa.String(length=128), nullable=True),
        sa.Column("password_algorithm", sa.String(length=32), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("login_key"),
        sa.UniqueConstraint("profile_id"),
        sa.UniqueConstraint("provider", "external_id", name="uq_users_provider_external"),
    )
    op.create_table(
        "oauth_accounts",
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=False),
        sa.Column("profile_url", sa.Text(), nullable=False),
        sa.Column("linked_at", sa.Float(), nullable=False),
        sa.Column("synced_at", sa.Float(), nullable=False),
        sa.Column("list_count", sa.Integer(), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id", "provider"),
    )
    op.create_table(
        "profile_favorites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("item_key", sa.String(length=2048), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "item_key", name="uq_favorites_profile_item"),
    )
    op.create_table(
        "profile_library",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("item_key", sa.String(length=2048), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("review", sa.Text(), nullable=False),
        sa.Column("external_provider", sa.String(length=32), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "item_key", name="uq_library_profile_item"),
    )
    op.create_index(
        "ix_library_external",
        "profile_library",
        ["external_provider", "external_id"],
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("last_seen_at", sa.Float(), nullable=True),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index("ix_sessions_user_expires", "sessions", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_sessions_user_expires", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_library_external", table_name="profile_library")
    op.drop_table("profile_library")
    op.drop_table("profile_favorites")
    op.drop_table("oauth_accounts")
    op.drop_table("users")
    op.drop_table("profiles")
