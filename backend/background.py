from __future__ import annotations

import threading
import hashlib
import logging
from collections.abc import Callable


logger = logging.getLogger("mangatemp")


class BackgroundTaskRunner:
    """Executor local simples: deduplica chaves e limita threads daemon ativas."""

    def __init__(self, max_concurrency: int) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency deve ser positivo.")
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._keys: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, key: str, operation: Callable[..., None], *args, **kwargs) -> bool:
        normalized = key.strip()
        if not normalized:
            raise ValueError("Task de background exige chave.")
        with self._lock:
            if normalized in self._keys:
                return True
            if not self._slots.acquire(blocking=False):
                return False
            self._keys.add(normalized)
        key_digest = hashlib.blake2s(
            normalized.encode("utf-8"), digest_size=8
        ).hexdigest()

        def run() -> None:
            try:
                operation(*args, **kwargs)
            except Exception as exc:
                logger.error(
                    "background task=%s error=%s",
                    key_digest,
                    type(exc).__name__,
                )
            finally:
                self._slots.release()
                with self._lock:
                    self._keys.discard(normalized)

        try:
            threading.Thread(
                target=run,
                name=f"kari-background-{key_digest}",
                daemon=True,
            ).start()
        except Exception:
            with self._lock:
                self._keys.discard(normalized)
            self._slots.release()
            raise
        return True

    def active_count(self) -> int:
        with self._lock:
            return len(self._keys)
