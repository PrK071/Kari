from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable


PathProvider = Callable[[], Path]


class _JsonObjectStore:
    def __init__(self, path: PathProvider) -> None:
        self._path = path
        self._lock = threading.RLock()

    def read(self) -> dict[str, dict]:
        with self._lock:
            path = self._path()
            try:
                if not path.exists():
                    return {}
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                return {}

    def write(self, data: dict[str, dict]) -> None:
        with self._lock:
            path = self._path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f"{path.suffix}.tmp")
            temporary.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(path)

    def get(self, key: str) -> dict | None:
        value = self.read().get(key)
        return dict(value) if isinstance(value, dict) else None

    def save(self, key: str, value: dict) -> None:
        with self._lock:
            data = self.read()
            data[key] = dict(value)
            self.write(data)


class JsonUserRepository:
    def __init__(self, path: PathProvider) -> None:
        self._store = _JsonObjectStore(path)

    def get(self, login_key: str) -> dict | None:
        return self._store.get(login_key)

    def get_by_profile_id(self, profile_id: str) -> dict | None:
        return next(
            (
                dict(user)
                for user in self._store.read().values()
                if isinstance(user, dict)
                and str(user.get("profile_id") or "") == profile_id
            ),
            None,
        )

    def save(self, login_key: str, user: dict) -> None:
        self._store.save(login_key, user)

    def all(self) -> dict[str, dict]:
        return self._store.read()


class JsonProfileRepository:
    def __init__(self, path: PathProvider) -> None:
        self._store = _JsonObjectStore(path)

    def get(self, profile_id: str) -> dict | None:
        return self._store.get(profile_id)

    def save(self, profile: dict) -> None:
        profile_id = str(profile.get("id") or "")
        if not profile_id:
            raise ValueError("Perfil sem id nao pode ser persistido.")
        self._store.save(profile_id, profile)

    def all(self) -> dict[str, dict]:
        return self._store.read()


class JsonSessionRepository:
    def __init__(self, path: PathProvider) -> None:
        self._store = _JsonObjectStore(path)

    def get(self, token: str) -> dict | None:
        return self._store.get(token)

    def save(self, token: str, session: dict) -> None:
        self._store.save(token, session)

    def revoke(self, token: str) -> bool:
        with self._store._lock:
            sessions = self._store.read()
            removed = sessions.pop(token, None) is not None
            if removed:
                self._store.write(sessions)
            return removed

    def purge_expired(self, now: float) -> int:
        with self._store._lock:
            sessions = self._store.read()
            active: dict[str, dict] = {}
            for token, session in sessions.items():
                try:
                    expires_at = float(session.get("expires", 0))
                except (AttributeError, TypeError, ValueError):
                    expires_at = 0
                if expires_at > now:
                    active[token] = session
            removed = len(sessions) - len(active)
            if removed:
                self._store.write(active)
            return removed

    def all(self) -> dict[str, dict]:
        return self._store.read()
