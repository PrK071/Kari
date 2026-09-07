from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from backend.search_runtime import ProviderCircuitBreaker, SearchCoalescer


def test_circuit_breaker_opens_then_closes_after_success() -> None:
    now = [100.0]
    breaker = ProviderCircuitBreaker(
        failure_threshold=3,
        cooldown_seconds=30,
        clock=lambda: now[0],
    )
    for _ in range(3):
        breaker.record("nexus", result_count=0, error="HTTPError")
    assert breaker.allow("nexus") == (False, "error")

    now[0] += 31
    assert breaker.allow("nexus") == (True, "")
    breaker.record("nexus", result_count=1)
    assert breaker.snapshot("nexus")["open"] is False


def test_circuit_breaker_opens_after_repeated_empty_results() -> None:
    breaker = ProviderCircuitBreaker(empty_threshold=3)
    for _ in range(3):
        breaker.record("source", result_count=0)
    assert breaker.allow("source") == (False, "empty")


def test_coalescer_runs_one_external_search_for_concurrent_callers() -> None:
    coalescer = SearchCoalescer()
    started = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()

    def operation() -> dict:
        nonlocal calls
        with lock:
            calls += 1
        started.set()
        release.wait(1)
        return {"items": ["one piece"]}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(coalescer.run, "one piece", operation, timeout=1) for _ in range(5)]
        assert started.wait(1)
        time.sleep(0.02)
        release.set()
        results = [future.result() for future in futures]

    assert calls == 1
    assert all(result == {"items": ["one piece"]} for result in results)
    assert len({id(result) for result in results}) == len(results)
