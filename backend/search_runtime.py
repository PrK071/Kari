from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar


T = TypeVar("T")


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    consecutive_empty: int = 0
    open_until: float = 0.0
    reason: str = ""


class ProviderCircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        empty_threshold: int = 5,
        cooldown_seconds: float = 5 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._empty_threshold = empty_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._states: dict[str, _CircuitState] = {}

    def allow(self, provider: str) -> tuple[bool, str]:
        key = provider.strip().casefold()
        with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            now = self._clock()
            if state.open_until > now:
                return False, state.reason or "circuit_open"
            if state.open_until:
                # Cooldown ended: one probe is allowed (half-open).
                state.open_until = 0.0
            return True, ""

    def record(
        self,
        provider: str,
        *,
        result_count: int,
        error: str | None = None,
        timeout: bool = False,
    ) -> None:
        key = provider.strip().casefold()
        with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            if error or timeout:
                state.consecutive_failures += 1
                state.consecutive_empty = 0
                state.reason = "timeout" if timeout else "error"
            elif result_count <= 0:
                state.consecutive_empty += 1
                state.consecutive_failures = 0
                state.reason = "empty"
            else:
                state.consecutive_failures = 0
                state.consecutive_empty = 0
                state.open_until = 0.0
                state.reason = ""
                return
            if (
                state.consecutive_failures >= self._failure_threshold
                or state.consecutive_empty >= self._empty_threshold
            ):
                state.open_until = self._clock() + self._cooldown_seconds

    def snapshot(self, provider: str) -> dict:
        key = provider.strip().casefold()
        with self._lock:
            state = self._states.get(key, _CircuitState())
            return {
                "consecutive_failures": state.consecutive_failures,
                "consecutive_empty": state.consecutive_empty,
                "open": state.open_until > self._clock(),
                "reason": state.reason,
            }


@dataclass
class _SearchFlight:
    event: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: Exception | None = None


class SearchCoalescer:
    """Single-flight for a complete external search, independent of providers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[str, _SearchFlight] = {}

    def run(self, key: str, operation: Callable[[], T], *, timeout: float) -> T:
        with self._lock:
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = _SearchFlight()
                self._flights[key] = flight
        if not leader:
            if not flight.event.wait(timeout):
                raise TimeoutError("Busca externa compartilhada excedeu o orçamento.")
            if flight.error is not None:
                raise flight.error
            return copy.deepcopy(flight.result)
        try:
            flight.result = operation()
            return copy.deepcopy(flight.result)
        except Exception as exc:
            flight.error = exc
            raise
        finally:
            flight.event.set()
            with self._lock:
                self._flights.pop(key, None)
