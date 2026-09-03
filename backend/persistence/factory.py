from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.persistence.base import ProfileRepository, SessionRepository, UserRepository
from backend.persistence.json import (
    JsonProfileRepository,
    JsonSessionRepository,
    JsonUserRepository,
)
from backend.persistence.postgres import (
    OAuthTokenCipher,
    PostgresProfileRepository,
    PostgresSessionRepository,
    PostgresUserRepository,
)


@dataclass(frozen=True)
class PersistenceRepositories:
    users: UserRepository
    profiles: ProfileRepository
    sessions: SessionRepository
    engine: Engine | None = None

    def ready(self) -> bool:
        if self.engine is None:
            return True
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


def build_repositories(
    *,
    backend: str,
    database_url: str,
    secret_key: str,
    users_path: Callable[[], Path],
    profiles_path: Callable[[], Path],
    sessions_path: Callable[[], Path],
) -> PersistenceRepositories:
    if backend == "json":
        return PersistenceRepositories(
            users=JsonUserRepository(users_path),
            profiles=JsonProfileRepository(profiles_path),
            sessions=JsonSessionRepository(sessions_path),
        )
    if backend != "postgres":
        raise ValueError(f"Backend de persistencia desconhecido: {backend}")

    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    cipher = OAuthTokenCipher(secret_key)
    return PersistenceRepositories(
        users=PostgresUserRepository(sessions),
        profiles=PostgresProfileRepository(sessions, cipher),
        sessions=PostgresSessionRepository(sessions),
        engine=engine,
    )
