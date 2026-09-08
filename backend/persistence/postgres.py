from __future__ import annotations

import base64
import difflib
import hashlib
import json
import time
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, selectinload, sessionmaker

from backend.persistence.models import (
    CatalogItemModel,
    FavoriteModel,
    HistoryEntryModel,
    LibraryEntryModel,
    OAuthAccountModel,
    ProfileModel,
    SessionModel,
    UserModel,
)
from backend.title_normalization import (
    normalize_aliases,
    normalize_match_text,
    source_identifier as normalize_source_identifier,
    stable_catalog_id,
)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class OAuthTokenCipher:
    def __init__(self, secret_key: str) -> None:
        derived = hashlib.sha256(secret_key.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, payload: dict) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def decrypt(self, ciphertext: str | None) -> dict:
        if not ciphertext:
            return {}
        try:
            value = json.loads(self._fernet.decrypt(ciphertext.encode("ascii")))
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


def _item_key(item: dict) -> str:
    return str(item.get("source_url") or item.get("id") or item.get("title") or "")


class PostgresCatalogRepository:
    """Persistent catalog repository; PostgreSQL is authoritative on web."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _json_safe(value):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))

    @classmethod
    def _record(cls, item: dict, now: float) -> dict | None:
        title = str(item.get("canonical_title") or item.get("title") or "").strip()
        provider = str(item.get("provider") or "").strip().casefold()
        source_url = str(item.get("source_url") or item.get("url") or "").strip()
        identifier = normalize_source_identifier(
            item.get("source_identifier") or source_url
        )
        normalized_title = normalize_match_text(title)
        if not title or not provider or not source_url or not identifier or not normalized_title:
            return None
        aliases = [
            str(alias).strip()
            for alias in (item.get("alternative_titles") or item.get("aliases") or [])
            if str(alias or "").strip()
        ]
        normalized_alias_values = [
            alias for alias in normalize_aliases(aliases) if alias != normalized_title
        ]
        source_key = stable_catalog_id("", identifier)
        item_id = stable_catalog_id(provider, identifier)
        try:
            chapter_count = max(0, int(
                item.get("chapter_count")
                or item.get("reported_chapter_count")
                or 0
            ))
        except (TypeError, ValueError):
            chapter_count = 0
        chapter_metadata = {
            "verified": bool(item.get("chapter_count_verified")),
            "latest_chapter": str(item.get("latest_chapter") or ""),
            "preview": list(item.get("chapter_preview") or [])[:3],
            "languages": list(item.get("chapter_languages") or [])[:8],
        }
        payload = cls._json_safe(dict(item))
        return {
            "id": item_id,
            "canonical_key": str(item.get("canonical_key") or normalized_title)[:512],
            "canonical_title": title[:512],
            "normalized_title": normalized_title[:512],
            "aliases": cls._json_safe(aliases),
            "normalized_aliases": cls._json_safe(normalized_alias_values),
            "search_text": " | ".join([normalized_title, *normalized_alias_values]),
            "provider": provider[:64],
            "source": str(item.get("source") or provider)[:128],
            "source_key": source_key,
            "source_identifier": identifier,
            "source_url": source_url,
            "cover_url": str(
                item.get("cover_original_url") or item.get("cover_url") or ""
            ),
            "genres": cls._json_safe(list(item.get("genres") or [])[:16]),
            "chapter_count": chapter_count,
            "chapter_metadata": cls._json_safe(chapter_metadata),
            "payload": payload,
            "is_home_ready": bool(item.get("catalog_home_ready")),
            "first_seen_at": now,
            "last_seen_at": now,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _payload(model: CatalogItemModel) -> dict:
        payload = dict(model.payload or {})
        payload.update({
            "id": str(payload.get("id") or model.id),
            "title": model.canonical_title,
            "canonical_key": model.canonical_key,
            "source_url": model.source_url,
            "provider": model.provider,
            "source": model.source,
            "genres": list(model.genres or []),
            "alternative_titles": list(model.aliases or []),
            "catalog_home_ready": bool(model.is_home_ready),
        })
        if not payload.get("cover_url") and model.cover_url:
            payload["cover_url"] = model.cover_url
        metadata = dict(model.chapter_metadata or {})
        payload["chapter_count"] = model.chapter_count
        payload["chapter_count_verified"] = bool(metadata.get("verified"))
        if metadata.get("latest_chapter"):
            payload["latest_chapter"] = metadata["latest_chapter"]
        if metadata.get("preview"):
            payload["chapter_preview"] = metadata["preview"]
        return payload

    def _ordered_candidates(self, query: str, limit: int) -> list[CatalogItemModel]:
        normalized = normalize_match_text(query)
        if not normalized or limit < 1:
            return []
        with self._sessions() as database:
            if database.bind is not None and database.bind.dialect.name == "postgresql":
                statement = select(CatalogItemModel).from_statement(text(
                    """
                    SELECT catalog_items.*
                    FROM catalog_items
                    WHERE normalized_title = :query
                       OR normalized_aliases @> CAST(:alias_json AS jsonb)
                       OR normalized_title LIKE :prefix
                       OR search_text % :query
                       OR search_text LIKE :contains
                    ORDER BY
                      CASE
                        WHEN normalized_title = :query THEN 0
                        WHEN normalized_aliases @> CAST(:alias_json AS jsonb) THEN 1
                        WHEN normalized_title LIKE :prefix THEN 2
                        ELSE 3
                      END,
                      similarity(search_text, :query) DESC,
                      last_seen_at DESC
                    LIMIT :candidate_limit
                    """
                ))
                return list(database.scalars(
                    statement,
                    {
                        "query": normalized,
                        "alias_json": json.dumps([normalized]),
                        "prefix": f"{normalized}%",
                        "contains": f"%{normalized}%",
                        "candidate_limit": max(limit * 4, 20),
                    },
                ))

            models = list(database.scalars(select(CatalogItemModel)))
            ranked: list[tuple[int, float, float, CatalogItemModel]] = []
            for model in models:
                aliases = list(model.normalized_aliases or [])
                if normalized == model.normalized_title:
                    tier = 0
                elif normalized in aliases:
                    tier = 1
                elif model.normalized_title.startswith(normalized):
                    tier = 2
                elif normalized in model.search_text:
                    tier = 3
                else:
                    similarity = difflib.SequenceMatcher(
                        None, normalized, model.search_text
                    ).ratio()
                    if similarity < 0.3:
                        continue
                    tier = 4
                similarity = difflib.SequenceMatcher(
                    None, normalized, model.search_text
                ).ratio()
                ranked.append((tier, -similarity, -model.last_seen_at, model))
            ranked.sort(key=lambda entry: entry[:3])
            return [entry[3] for entry in ranked[:max(limit * 4, 20)]]

    def search(self, query: str, limit: int) -> list[dict]:
        result: list[dict] = []
        seen_canonical: set[str] = set()
        for model in self._ordered_candidates(query, limit):
            if model.canonical_key in seen_canonical:
                continue
            seen_canonical.add(model.canonical_key)
            result.append(self._payload(model))
            if len(result) >= limit:
                break
        return result

    def upsert_item(self, item: dict) -> bool:
        return self.upsert_many([item]) == 1

    def upsert_many(self, items: list[dict]) -> int:
        now = time.time()
        records = [record for item in items if (record := self._record(item, now))]
        if not records:
            return 0
        immutable = {"id", "provider", "source_key", "first_seen_at", "created_at"}
        with self._sessions.begin() as database:
            dialect = database.bind.dialect.name if database.bind is not None else ""
            if dialect == "postgresql":
                statement = postgres_insert(CatalogItemModel).values(records)
                statement = statement.on_conflict_do_update(
                    constraint="uq_catalog_provider_source",
                    set_={
                        key: getattr(statement.excluded, key)
                        for key in records[0]
                        if key not in immutable
                    },
                )
                database.execute(statement)
            elif dialect == "sqlite":
                statement = sqlite_insert(CatalogItemModel).values(records)
                statement = statement.on_conflict_do_update(
                    index_elements=["provider", "source_key"],
                    set_={
                        key: getattr(statement.excluded, key)
                        for key in records[0]
                        if key not in immutable
                    },
                )
                database.execute(statement)
            else:
                for record in records:
                    database.merge(CatalogItemModel(**record))
        return len(records)

    def get_by_source(self, provider: str, source: str) -> dict | None:
        identifier = normalize_source_identifier(source)
        item_id = stable_catalog_id(provider, identifier)
        with self._sessions() as database:
            model = database.get(CatalogItemModel, item_id)
            return self._payload(model) if model else None

    def mark_seen(self, provider: str, source: str, seen_at: float | None = None) -> bool:
        identifier = normalize_source_identifier(source)
        item_id = stable_catalog_id(provider, identifier)
        with self._sessions.begin() as database:
            model = database.get(CatalogItemModel, item_id)
            if model is None:
                return False
            model.last_seen_at = seen_at or time.time()
            model.updated_at = time.time()
            return True

    def list_home(self, limit: int) -> list[dict]:
        with self._sessions() as database:
            models = list(database.scalars(
                select(CatalogItemModel)
                .where(CatalogItemModel.is_home_ready.is_(True))
                .order_by(CatalogItemModel.last_seen_at.desc())
                .limit(max(1, limit))
            ))
            return [self._payload(model) for model in models]

    def prune_stale(self, before: float) -> int:
        with self._sessions.begin() as database:
            result = database.execute(
                delete(CatalogItemModel).where(CatalogItemModel.last_seen_at < before)
            )
            return int(result.rowcount or 0)


class PostgresUserRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @staticmethod
    def _payload(model: UserModel) -> dict:
        payload = {
            "id": model.id,
            "username": model.username,
            "email": model.email,
            "profile_id": model.profile_id,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
        if model.provider:
            payload["provider"] = model.provider
            payload[f"{model.provider}_id"] = model.external_id or ""
        if model.password_hash:
            payload["password_hash"] = model.password_hash
            payload["salt"] = model.password_salt or ""
            payload["password_algorithm"] = model.password_algorithm or "pbkdf2_sha256"
        return payload

    def get(self, login_key: str) -> dict | None:
        with self._sessions() as database:
            model = database.scalar(select(UserModel).where(UserModel.login_key == login_key))
            return self._payload(model) if model else None

    def get_by_profile_id(self, profile_id: str) -> dict | None:
        with self._sessions() as database:
            model = database.scalar(select(UserModel).where(UserModel.profile_id == profile_id))
            return self._payload(model) if model else None

    def save(self, login_key: str, user: dict) -> None:
        now = time.time()
        with self._sessions.begin() as database:
            model = database.scalar(select(UserModel).where(UserModel.login_key == login_key))
            if model is None:
                model = UserModel(
                    id=str(user.get("id") or uuid4().hex),
                    login_key=login_key,
                    username=str(user.get("username") or ""),
                    profile_id=str(user.get("profile_id") or ""),
                    created_at=float(user.get("created_at") or now),
                    updated_at=float(user.get("updated_at") or now),
                )
                database.add(model)
            model.username = str(user.get("username") or model.username)
            model.email = str(user.get("email") or "")
            model.profile_id = str(user.get("profile_id") or model.profile_id)
            model.provider = str(user.get("provider") or "") or None
            model.external_id = (
                str(user.get(f"{model.provider}_id") or "") or None
                if model.provider
                else None
            )
            model.password_hash = str(user.get("password_hash") or "") or None
            model.password_salt = str(user.get("salt") or "") or None
            model.password_algorithm = (
                str(user.get("password_algorithm") or "pbkdf2_sha256")
                if model.password_hash
                else None
            )
            model.updated_at = float(user.get("updated_at") or now)

    def all(self) -> dict[str, dict]:
        with self._sessions() as database:
            models = database.scalars(select(UserModel)).all()
            return {model.login_key: self._payload(model) for model in models}


class PostgresProfileRepository:
    def __init__(self, sessions: sessionmaker[Session], cipher: OAuthTokenCipher) -> None:
        self._sessions = sessions
        self._cipher = cipher

    @staticmethod
    def _query():
        return select(ProfileModel).options(
            selectinload(ProfileModel.favorites),
            selectinload(ProfileModel.history),
            selectinload(ProfileModel.library),
            selectinload(ProfileModel.oauth_accounts),
        )

    def _payload(self, model: ProfileModel) -> dict:
        profile = {
            "id": model.id,
            "display_name": model.display_name,
            "avatar_url": model.avatar_url,
            "background_url": model.background_url,
            "home_background_url": model.home_background_url,
            "favorites": [dict(item.data) for item in model.favorites],
            "history": [dict(item.data) for item in model.history],
            "library": [dict(item.data) for item in model.library],
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
        links: dict[str, dict] = {}
        tokens: dict[str, dict] = {}
        for account in model.oauth_accounts:
            links[account.provider] = {
                "id": account.external_user_id,
                "name": account.name,
                "avatar": account.avatar_url,
                "url": account.profile_url,
                "linked_at": account.linked_at,
                "synced_at": account.synced_at,
                "list_count": account.list_count,
                "matched_count": account.matched_count,
            }
            decrypted = self._cipher.decrypt(account.token_ciphertext)
            if decrypted:
                tokens[account.provider] = decrypted
        if links:
            profile["links"] = links
        if tokens:
            profile["_tokens"] = tokens
        return profile

    def get(self, profile_id: str) -> dict | None:
        with self._sessions() as database:
            model = database.scalar(self._query().where(ProfileModel.id == profile_id))
            return self._payload(model) if model else None

    def save(self, profile: dict) -> None:
        profile_id = str(profile.get("id") or "")
        if not profile_id:
            raise ValueError("Perfil sem id nao pode ser persistido.")
        now = time.time()
        with self._sessions.begin() as database:
            model = database.get(ProfileModel, profile_id)
            if model is None:
                model = ProfileModel(
                    id=profile_id,
                    display_name=str(profile.get("display_name") or "Leitor"),
                    created_at=float(profile.get("created_at") or now),
                    updated_at=float(profile.get("updated_at") or now),
                )
                database.add(model)
                database.flush()
            model.display_name = str(profile.get("display_name") or "Leitor")
            model.avatar_url = str(profile.get("avatar_url") or "")
            model.background_url = str(profile.get("background_url") or "")
            model.home_background_url = str(profile.get("home_background_url") or "")
            model.updated_at = float(profile.get("updated_at") or now)

            database.execute(delete(FavoriteModel).where(FavoriteModel.profile_id == profile_id))
            favorite_keys: set[str] = set()
            for position, item in enumerate(profile.get("favorites") or []):
                item_key = _item_key(item) if isinstance(item, dict) else ""
                if item_key and item_key not in favorite_keys:
                    favorite_keys.add(item_key)
                    database.add(
                        FavoriteModel(
                            profile_id=profile_id,
                            item_key=item_key,
                            position=position,
                            data=dict(item),
                        )
                    )

            database.execute(delete(HistoryEntryModel).where(HistoryEntryModel.profile_id == profile_id))
            history_keys: set[str] = set()
            for position, item in enumerate(profile.get("history") or []):
                item_key = _item_key(item) if isinstance(item, dict) else ""
                if item_key and item_key not in history_keys:
                    history_keys.add(item_key)
                    database.add(
                        HistoryEntryModel(
                            profile_id=profile_id,
                            item_key=item_key,
                            position=position,
                            data=dict(item),
                        )
                    )

            database.execute(delete(LibraryEntryModel).where(LibraryEntryModel.profile_id == profile_id))
            library_keys: set[str] = set()
            for position, item in enumerate(profile.get("library") or []):
                item_key = _item_key(item) if isinstance(item, dict) else ""
                if item_key and item_key not in library_keys:
                    library_keys.add(item_key)
                    database.add(
                        LibraryEntryModel(
                            profile_id=profile_id,
                            item_key=item_key,
                            position=position,
                            status=str(item.get("status") or "COMPLETED"),
                            score=item.get("score"),
                            review=str(item.get("review") or ""),
                            external_provider=str(item.get("external_provider") or "") or None,
                            external_id=str(item.get("external_id") or "") or None,
                            updated_at=float(item.get("updated_at") or now),
                            data=dict(item),
                        )
                    )

            database.execute(delete(OAuthAccountModel).where(OAuthAccountModel.profile_id == profile_id))
            links = profile.get("links") if isinstance(profile.get("links"), dict) else {}
            token_map = profile.get("_tokens") if isinstance(profile.get("_tokens"), dict) else {}
            for provider in sorted(set(links) | set(token_map)):
                link = links.get(provider) if isinstance(links.get(provider), dict) else {}
                provider_tokens = token_map.get(provider) if isinstance(token_map.get(provider), dict) else {}
                database.add(
                    OAuthAccountModel(
                        profile_id=profile_id,
                        provider=str(provider),
                        external_user_id=str(link.get("id") or ""),
                        name=str(link.get("name") or ""),
                        avatar_url=str(link.get("avatar") or ""),
                        profile_url=str(link.get("url") or ""),
                        linked_at=float(link.get("linked_at") or now),
                        synced_at=float(link.get("synced_at") or 0),
                        list_count=int(link.get("list_count") or 0),
                        matched_count=int(link.get("matched_count") or 0),
                        token_ciphertext=(
                            self._cipher.encrypt(provider_tokens)
                            if provider_tokens
                            else None
                        ),
                    )
                )

    def all(self) -> dict[str, dict]:
        with self._sessions() as database:
            models = database.scalars(self._query()).all()
            return {model.id: self._payload(model) for model in models}


class PostgresSessionRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, token: str) -> dict | None:
        digest = token_digest(token)
        with self._sessions() as database:
            model = database.scalar(
                select(SessionModel)
                .options(selectinload(SessionModel.user))
                .where(
                    SessionModel.token_digest == digest,
                    SessionModel.revoked_at.is_(None),
                )
            )
            if model is None:
                return None
            return {
                "profile_id": model.user.profile_id,
                "username": model.user.username,
                "expires": model.expires_at,
                "created_at": model.created_at,
            }

    def save(self, token: str, session: dict) -> None:
        profile_id = str(session.get("profile_id") or "")
        now = time.time()
        digest = token_digest(token)
        with self._sessions.begin() as database:
            user = database.scalar(select(UserModel).where(UserModel.profile_id == profile_id))
            if user is None:
                raise ValueError("Sessao requer usuario persistido.")
            model = database.scalar(
                select(SessionModel).where(SessionModel.token_digest == digest)
            )
            if model is None:
                model = SessionModel(
                    id=uuid4().hex,
                    token_digest=digest,
                    user_id=user.id,
                    created_at=float(session.get("created_at") or now),
                    expires_at=float(session.get("expires") or now),
                    last_seen_at=now,
                )
                database.add(model)
            else:
                model.user_id = user.id
                model.expires_at = float(session.get("expires") or model.expires_at)
                model.revoked_at = None

    def revoke(self, token: str) -> bool:
        digest = token_digest(token)
        with self._sessions.begin() as database:
            model = database.scalar(
                select(SessionModel).where(
                    SessionModel.token_digest == digest,
                    SessionModel.revoked_at.is_(None),
                )
            )
            if model is None:
                return False
            model.revoked_at = time.time()
            return True

    def purge_expired(self, now: float) -> int:
        with self._sessions.begin() as database:
            result = database.execute(
                delete(SessionModel).where(
                    or_(
                        SessionModel.expires_at <= now,
                        SessionModel.revoked_at.is_not(None),
                    )
                )
            )
            return int(result.rowcount or 0)

    def all(self) -> dict[str, dict]:
        with self._sessions() as database:
            rows = database.execute(
                select(SessionModel, UserModel)
                .join(UserModel, UserModel.id == SessionModel.user_id)
            ).all()
            return {
                session.token_digest: {
                    "profile_id": user.profile_id,
                    "username": user.username,
                    "expires": session.expires_at,
                    "created_at": session.created_at,
                    "revoked_at": session.revoked_at,
                }
                for session, user in rows
            }
