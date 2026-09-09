from __future__ import annotations

import difflib
import sys
import threading
import time
from dataclasses import dataclass

from backend.title_normalization import (
    normalize_aliases,
    normalize_match_text,
    source_identifier,
    stable_catalog_id,
)


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    id: str
    canonical_key: str
    canonical_title: str
    normalized_title: str
    aliases: tuple[str, ...]
    normalized_aliases: tuple[str, ...]
    provider: str
    source: str
    source_url: str
    cover_url: str
    genres: tuple[str, ...]
    chapter_count: int
    chapter_count_verified: bool
    latest_chapter: str
    chapter_preview: tuple[str, ...]
    chapter_languages: tuple[str, ...]
    last_seen_at: float
    is_home_ready: bool

    def payload(self) -> dict:
        return {
            "id": self.id,
            "canonical_key": self.canonical_key,
            "title": self.canonical_title,
            "normalized_title": self.normalized_title,
            "alternative_titles": list(self.aliases),
            "provider": self.provider,
            "source": self.source,
            "source_url": self.source_url,
            "cover_url": self.cover_url,
            "genres": list(self.genres),
            "chapter_count": self.chapter_count,
            "chapter_count_verified": self.chapter_count_verified,
            "latest_chapter": self.latest_chapter,
            "chapter_preview": list(self.chapter_preview),
            "chapter_languages": list(self.chapter_languages),
            "_catalog_last_seen_at": self.last_seen_at,
            "catalog_home_ready": self.is_home_ready,
        }


def _compact_entry(item: dict, *, default_seen_at: float = 0.0) -> _CatalogEntry | None:
    title = str(item.get("canonical_title") or item.get("title") or "").strip()
    provider = str(item.get("provider") or "").strip().casefold()
    source_url = str(item.get("source_url") or item.get("url") or "").strip()
    identifier = source_identifier(item.get("source_identifier") or source_url)
    normalized_title = str(item.get("normalized_title") or normalize_match_text(title)).strip()
    if not title or not provider or not source_url or not identifier or not normalized_title:
        return None

    aliases = tuple(dict.fromkeys(
        str(alias).strip()
        for alias in (item.get("alternative_titles") or item.get("aliases") or [])
        if str(alias or "").strip()
    ))
    normalized_alias_values = tuple(
        alias
        for alias in normalize_aliases(aliases)
        if alias != normalized_title
    )
    try:
        chapter_count = max(0, int(item.get("chapter_count") or 0))
    except (TypeError, ValueError):
        chapter_count = 0
    try:
        last_seen_at = float(
            item.get("_catalog_last_seen_at")
            or item.get("last_seen_at")
            or default_seen_at
            or 0.0
        )
    except (TypeError, ValueError):
        last_seen_at = float(default_seen_at or 0.0)

    return _CatalogEntry(
        id=str(item.get("catalog_id") or stable_catalog_id(provider, identifier)),
        canonical_key=str(item.get("canonical_key") or normalized_title),
        canonical_title=title,
        normalized_title=normalized_title,
        aliases=aliases,
        normalized_aliases=normalized_alias_values,
        provider=provider,
        source=str(item.get("source") or provider),
        source_url=source_url,
        cover_url=str(item.get("cover_original_url") or item.get("cover_url") or ""),
        genres=tuple(str(genre) for genre in (item.get("genres") or []) if str(genre or "").strip()),
        chapter_count=chapter_count,
        chapter_count_verified=bool(item.get("chapter_count_verified")),
        latest_chapter=str(item.get("latest_chapter") or ""),
        chapter_preview=tuple(str(value) for value in (item.get("chapter_preview") or [])[:3]),
        chapter_languages=tuple(str(value) for value in (item.get("chapter_languages") or [])[:8]),
        last_seen_at=last_seen_at,
        is_home_ready=bool(item.get("catalog_home_ready") or item.get("is_home_ready")),
    )


def _exact_maps(
    entries: tuple[_CatalogEntry, ...],
) -> tuple[dict[str, tuple[_CatalogEntry, ...]], dict[str, tuple[_CatalogEntry, ...]]]:
    titles: dict[str, list[_CatalogEntry]] = {}
    aliases: dict[str, list[_CatalogEntry]] = {}
    for entry in entries:
        titles.setdefault(entry.normalized_title, []).append(entry)
        for alias in entry.normalized_aliases:
            aliases.setdefault(alias, []).append(entry)
    return (
        {key: tuple(values) for key, values in titles.items()},
        {key: tuple(values) for key, values in aliases.items()},
    )


def _deep_size(value: object, seen: set[int] | None = None) -> int:
    seen = seen or set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_deep_size(key, seen) + _deep_size(item, seen) for key, item in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        size += sum(_deep_size(item, seen) for item in value)
    elif hasattr(value, "__dataclass_fields__"):
        size += sum(_deep_size(getattr(value, field), seen) for field in value.__dataclass_fields__)
    return size


class CatalogMemoryIndex:
    """Disposable compact search snapshot. PostgreSQL remains authoritative."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: tuple[_CatalogEntry, ...] = ()
        self._title_exact: dict[str, tuple[_CatalogEntry, ...]] = {}
        self._alias_exact: dict[str, tuple[_CatalogEntry, ...]] = {}
        self._ready = False
        self._rebuild_ms = 0.0
        self._memory_bytes = 0

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def rebuild(self, items: list[dict]) -> dict[str, int | float | bool]:
        started_at = time.perf_counter()
        entries = tuple(
            entry
            for item in items
            if isinstance(item, dict)
            and (entry := _compact_entry(item)) is not None
        )
        title_exact, alias_exact = _exact_maps(entries)
        memory_bytes = _deep_size((entries, title_exact, alias_exact))
        rebuild_ms = round((time.perf_counter() - started_at) * 1000, 2)
        with self._lock:
            self._entries = entries
            self._title_exact = title_exact
            self._alias_exact = alias_exact
            self._ready = True
            self._rebuild_ms = rebuild_ms
            self._memory_bytes = memory_bytes
        return self.stats()

    def upsert_many(self, items: list[dict]) -> int:
        now = time.time()
        updates = {
            entry.id: entry
            for item in items
            if isinstance(item, dict)
            and (entry := _compact_entry(item, default_seen_at=now)) is not None
        }
        if not updates:
            return 0
        with self._lock:
            merged = {entry.id: entry for entry in self._entries}
            merged.update(updates)
            self._entries = tuple(merged.values())
            self._title_exact, self._alias_exact = _exact_maps(self._entries)
            self._memory_bytes = _deep_size(
                (self._entries, self._title_exact, self._alias_exact)
            )
        return len(updates)

    def search(self, query: str, limit: int) -> list[dict]:
        normalized = normalize_match_text(query)
        if not normalized or limit < 1:
            return []
        with self._lock:
            entries = self._entries
            title_exact = self._title_exact
            alias_exact = self._alias_exact

        exact = title_exact.get(normalized) or alias_exact.get(normalized)
        if exact:
            ordered = sorted(exact, key=lambda entry: entry.last_seen_at, reverse=True)
            return [entry.payload() for entry in ordered[:limit]]

        prefix: list[_CatalogEntry] = []
        contains: list[_CatalogEntry] = []
        query_tokens = set(normalized.split())
        for entry in entries:
            candidates = (entry.normalized_title, *entry.normalized_aliases)
            if any(candidate.startswith(normalized) for candidate in candidates):
                prefix.append(entry)
            elif any(normalized in candidate for candidate in candidates) or (
                query_tokens
                and any(query_tokens.issubset(set(candidate.split())) for candidate in candidates)
            ):
                contains.append(entry)
        matched = prefix or contains
        if matched:
            matched.sort(key=lambda entry: entry.last_seen_at, reverse=True)
            return [entry.payload() for entry in matched[:limit]]

        fuzzy: list[tuple[float, float, _CatalogEntry]] = []
        for entry in entries:
            similarity = max(
                (
                    difflib.SequenceMatcher(None, normalized, candidate).ratio()
                    for candidate in (entry.normalized_title, *entry.normalized_aliases)
                ),
                default=0.0,
            )
            if similarity >= 0.48:
                fuzzy.append((-similarity, -entry.last_seen_at, entry))
        fuzzy.sort(key=lambda value: value[:2])
        return [entry.payload() for _, _, entry in fuzzy[:limit]]

    def list_home(self, limit: int) -> list[dict]:
        if limit < 1:
            return []
        with self._lock:
            entries = self._entries
        ready = sorted(
            (entry for entry in entries if entry.is_home_ready),
            key=lambda entry: entry.last_seen_at,
            reverse=True,
        )
        return [entry.payload() for entry in ready[:limit]]

    def snapshot(self) -> list[dict]:
        with self._lock:
            entries = self._entries
        return [entry.payload() for entry in entries]

    def stats(self) -> dict[str, int | float | bool]:
        with self._lock:
            return {
                "ready": self._ready,
                "item_count": len(self._entries),
                "rebuild_ms": self._rebuild_ms,
                "memory_bytes": self._memory_bytes,
            }
