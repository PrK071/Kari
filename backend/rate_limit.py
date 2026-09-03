from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int = 0


class RateLimitBackend(Protocol):
    def consume(
        self,
        key: str,
        policy: RateLimitPolicy,
        *,
        now: float | None = None,
    ) -> RateLimitDecision: ...


class MemoryRateLimitBackend:
    """Sliding-window limiter for a single API process."""

    def __init__(self, *, max_keys: int = 20_000, retention_seconds: int = 3600) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._max_keys = max_keys
        self._retention_seconds = retention_seconds

    def consume(
        self,
        key: str,
        policy: RateLimitPolicy,
        *,
        now: float | None = None,
    ) -> RateLimitDecision:
        current = time.monotonic() if now is None else now
        cutoff = current - policy.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= policy.limit:
                retry_after = max(1, math.ceil(events[0] + policy.window_seconds - current))
                return RateLimitDecision(False, retry_after)
            events.append(current)
            if len(self._events) > self._max_keys:
                self._prune(current - self._retention_seconds)
            return RateLimitDecision(True)

    def _prune(self, cutoff: float) -> None:
        stale = [key for key, events in self._events.items() if not events or events[-1] <= cutoff]
        for key in stale:
            self._events.pop(key, None)
        while len(self._events) > self._max_keys:
            self._events.pop(next(iter(self._events)))

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class RateLimiter:
    def __init__(self, backend: RateLimitBackend) -> None:
        self.backend = backend

    @staticmethod
    def key(scope: str, dimension: str, value: str) -> str:
        digest = hashlib.blake2s(value.encode("utf-8"), digest_size=16).hexdigest()
        return f"{scope}:{dimension}:{digest}"

    def check(
        self,
        scope: str,
        policy: RateLimitPolicy,
        dimensions: dict[str, str],
    ) -> RateLimitDecision:
        for dimension, value in dimensions.items():
            if not value:
                continue
            decision = self.backend.consume(self.key(scope, dimension, value), policy)
            if not decision.allowed:
                return decision
        return RateLimitDecision(True)
