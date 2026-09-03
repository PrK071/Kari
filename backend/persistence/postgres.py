from __future__ import annotations

import base64
import hashlib
import json
import time
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from backend.persistence.models import (
    FavoriteModel,
    LibraryEntryModel,
    OAuthAccountModel,
    ProfileModel,
    SessionModel,
    UserModel,
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
