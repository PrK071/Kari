"""Add persistent searchable catalog.

Revision ID: 20260907_0003
Revises: 20260907_0002
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260907_0003"
down_revision = "20260907_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "catalog_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("canonical_key", sa.String(length=512), nullable=False),
        sa.Column("canonical_title", sa.String(length=512), nullable=False),
        sa.Column("normalized_title", sa.String(length=512), nullable=False),
        sa.Column("aliases", document, nullable=False),
        sa.Column("normalized_aliases", document, nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("source_identifier", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("cover_url", sa.Text(), nullable=False),
        sa.Column("genres", document, nullable=False),
        sa.Column("chapter_count", sa.Integer(), nullable=False),
        sa.Column("chapter_metadata", document, nullable=False),
        sa.Column("payload", document, nullable=False),
        sa.Column("is_home_ready", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.Float(), nullable=False),
        sa.Column("last_seen_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "source_key", name="uq_catalog_provider_source"),
    )
    op.create_index("ix_catalog_normalized_title", "catalog_items", ["normalized_title"])
    op.create_index("ix_catalog_canonical_key", "catalog_items", ["canonical_key"])
    op.create_index(
        "ix_catalog_home_ready_seen",
        "catalog_items",
        ["is_home_ready", "last_seen_at"],
    )
    if is_postgres:
        op.create_index(
            "ix_catalog_search_text_trgm",
            "catalog_items",
            ["search_text"],
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        )
        op.create_index(
            "ix_catalog_normalized_aliases_gin",
            "catalog_items",
            ["normalized_aliases"],
            postgresql_using="gin",
        )


def downgrade() -> None:
    op.drop_table("catalog_items")
    # pg_trgm can be shared by other schemas; never drop it during rollback.
