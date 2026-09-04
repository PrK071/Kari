from __future__ import annotations

import copy
import hashlib
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


class WorkCapacityExceeded(RuntimeError):
    pass


class BoundedExecutor:
    """Pool compartilhado que limita threads e trabalhos pendentes."""

    def __init__(self, *, max_workers: int, max_pending: int) -> None:
        if max_workers < 1 or max_pending < max_workers:
            raise ValueError("Capacidade do executor deve comportar os workers.")
        self._slots = threading.BoundedSemaphore(max_pending)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="kari-scraper",
        )

    def submit(self, operation: Callable[..., T], *args, **kwargs) -> Future[T]:
        if not self._slots.acquire(blocking=False):
            raise WorkCapacityExceeded("Fila global de scrapers atingiu o limite.")
        try:
            future = self._executor.submit(operation, *args, **kwargs)
        except Exception:
            self._slots.release()
            raise
        future.add_done_callback(lambda _future: self._slots.release())
        return future

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)


@dataclass
class _Flight(Generic[T]):
    event: threading.Event = field(default_factory=threading.Event)
    result: T | None = None
    error: Exception | None = None


class BoundedWorkCoordinator:
    def __init__(
        self,
        *,
        max_global: int,
        max_per_source: int,
        queue_timeout: float = 2.0,
        duplicate_timeout: float = 30.0,
    ) -> None:
        if max_global < 1 or max_per_source < 1:
            raise ValueError("Limites de concorrencia devem ser positivos.")
        self._global = threading.BoundedSemaphore(max_global)
        self._max_per_source = max_per_source
        self._queue_timeout = queue_timeout
        self._duplicate_timeout = duplicate_timeout
        self._source_lock = threading.Lock()
        self._source_slots: dict[str, threading.BoundedSemaphore] = {}
        self._flight_lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}

    @staticmethod
    def _operation_id(source: str, operation_key: str) -> str:
        digest = hashlib.blake2s(operation_key.encode("utf-8"), digest_size=16).hexdigest()
        return f"{source.casefold()}:{digest}"

    def _source_slot(self, source: str) -> threading.BoundedSemaphore:
        normalized = source.strip().casefold() or "unknown"
        with self._source_lock:
            return self._source_slots.setdefault(
                normalized,
                threading.BoundedSemaphore(self._max_per_source),
            )

    def run(self, source: str, operation_key: str, operation: Callable[[], T]) -> T:
        operation_id = self._operation_id(source, operation_key)
        with self._flight_lock:
            flight = self._flights.get(operation_id)
            leader = flight is None
            if leader:
                flight = _Flight()
                self._flights[operation_id] = flight

        if not leader:
            if not flight.event.wait(self._duplicate_timeout):
                raise WorkCapacityExceeded("Operacao duplicada excedeu o tempo de espera.")
            if flight.error is not None:
                raise flight.error
            return copy.deepcopy(flight.result)

        source_slot = self._source_slot(source)
        source_acquired = source_slot.acquire(timeout=self._queue_timeout)
        global_acquired = False
        try:
            if not source_acquired:
                raise WorkCapacityExceeded("Limite de concorrencia da fonte atingido.")
            global_acquired = self._global.acquire(timeout=self._queue_timeout)
            if not global_acquired:
                raise WorkCapacityExceeded("Limite global de scrapers atingido.")
            flight.result = operation()
            return copy.deepcopy(flight.result)
        except Exception as exc:
            flight.error = exc
            raise
        finally:
            if global_acquired:
                self._global.release()
            if source_acquired:
                source_slot.release()
            flight.event.set()
            with self._flight_lock:
                self._flights.pop(operation_id, None)
