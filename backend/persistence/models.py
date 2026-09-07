from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class ProfileModel(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(48), nullable=False)
    avatar_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    background_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    home_background_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)

    favorites: Mapped[list[FavoriteModel]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="FavoriteModel.position",
    )
    history: Mapped[list[HistoryEntryModel]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="HistoryEntryModel.position",
    )
    library: Mapped[list[LibraryEntryModel]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="LibraryEntryModel.position",
    )
    oauth_accounts: Mapped[list[OAuthAccountModel]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
    )


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_users_provider_external"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    login_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_salt: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_algorithm: Mapped[str | None] = mapped_column(String(32), nullable=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)

    sessions: Mapped[list[SessionModel]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    last_seen_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    user: Mapped[UserModel] = relationship(back_populates="sessions")


class FavoriteModel(Base):
    __tablename__ = "profile_favorites"
    __table_args__ = (
        UniqueConstraint("profile_id", "item_key", name="uq_favorites_profile_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    profile: Mapped[ProfileModel] = relationship(back_populates="favorites")


class HistoryEntryModel(Base):
    __tablename__ = "profile_history"
    __table_args__ = (
        UniqueConstraint("profile_id", "item_key", name="uq_history_profile_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    profile: Mapped[ProfileModel] = relationship(back_populates="history")


class LibraryEntryModel(Base):
    __tablename__ = "profile_library"
    __table_args__ = (
        UniqueConstraint("profile_id", "item_key", name="uq_library_profile_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review: Mapped[str] = mapped_column(Text, nullable=False, default="")
    external_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    profile: Mapped[ProfileModel] = relationship(back_populates="library")


class OAuthAccountModel(Base):
    __tablename__ = "oauth_accounts"

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    avatar_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    profile_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    linked_at: Mapped[float] = mapped_column(Float, nullable=False)
    synced_at: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    list_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile: Mapped[ProfileModel] = relationship(back_populates="oauth_accounts")


class CatalogItemModel(Base):
    __tablename__ = "catalog_items"
    __table_args__ = (
        UniqueConstraint("provider", "source_key", name="uq_catalog_provider_source"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_title: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(512), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON_DOCUMENT, nullable=False)
    normalized_aliases: Mapped[list] = mapped_column(JSON_DOCUMENT, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_identifier: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    cover_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    genres: Mapped[list] = mapped_column(JSON_DOCUMENT, nullable=False)
    chapter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapter_metadata: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    is_home_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[float] = mapped_column(Float, nullable=False)
    last_seen_at: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


Index("ix_sessions_user_expires", SessionModel.user_id, SessionModel.expires_at)
Index("ix_library_external", LibraryEntryModel.external_provider, LibraryEntryModel.external_id)
Index("ix_catalog_normalized_title", CatalogItemModel.normalized_title)
Index("ix_catalog_canonical_key", CatalogItemModel.canonical_key)
Index("ix_catalog_home_ready_seen", CatalogItemModel.is_home_ready, CatalogItemModel.last_seen_at)
# O indice com operator class de pg_trgm pertence a migration Alembic. Declara-lo
# no metadata quebra create_all() quando o search_path nao inclui o schema da
# extensao (por exemplo, testes PostgreSQL em schema isolado).
Index(
    "ix_catalog_normalized_aliases_gin",
    CatalogItemModel.normalized_aliases,
    postgresql_using="gin",
)
