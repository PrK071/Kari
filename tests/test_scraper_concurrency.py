from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.concurrency import BoundedExecutor, BoundedWorkCoordinator, WorkCapacityExceeded


class ScraperConcurrencyTests(unittest.TestCase):
    def test_shared_executor_bounds_workers_and_pending_work(self) -> None:
        executor = BoundedExecutor(max_workers=2, max_pending=2)
        started = threading.Barrier(3)
        release = threading.Event()

        def blocking_operation(value: int) -> int:
            started.wait(timeout=1)
            release.wait(timeout=1)
            return value

        first = executor.submit(blocking_operation, 1)
        second = executor.submit(blocking_operation, 2)
        started.wait(timeout=1)
        with self.assertRaises(WorkCapacityExceeded):
            executor.submit(lambda: 3)
        release.set()
        self.assertEqual({first.result(timeout=1), second.result(timeout=1)}, {1, 2})
        self.assertEqual(executor.submit(lambda: 4).result(timeout=1), 4)
        executor.shutdown()

    def test_duplicate_operations_share_one_execution(self) -> None:
        coordinator = BoundedWorkCoordinator(max_global=4, max_per_source=2)
        calls = 0
        lock = threading.Lock()

        def operation() -> dict:
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.05)
            return {"items": ["result"]}

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _: coordinator.run("source-a", "same-query", operation),
                    range(8),
                )
            )

        self.assertEqual(calls, 1)
        self.assertTrue(all(result == {"items": ["result"]} for result in results))
        self.assertEqual(len({id(result) for result in results}), len(results))

    def test_global_and_per_source_limits_bound_parallel_work(self) -> None:
        coordinator = BoundedWorkCoordinator(
            max_global=3,
            max_per_source=2,
            queue_timeout=1,
        )
        active_global = 0
        active_by_source = {"a": 0, "b": 0}
        observed_global = 0
        observed_by_source = {"a": 0, "b": 0}
        lock = threading.Lock()

        def operation(source: str, value: int) -> int:
            nonlocal active_global, observed_global
            with lock:
                active_global += 1
                active_by_source[source] += 1
                observed_global = max(observed_global, active_global)
                observed_by_source[source] = max(
                    observed_by_source[source],
                    active_by_source[source],
                )
            time.sleep(0.02)
            with lock:
                active_global -= 1
                active_by_source[source] -= 1
            return value

        jobs = [("a" if index % 2 else "b", index) for index in range(12)]
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = [
                executor.submit(
                    coordinator.run,
                    source,
                    f"job-{value}",
                    lambda source=source, value=value: operation(source, value),
                )
                for source, value in jobs
            ]
            self.assertEqual(sorted(future.result() for future in futures), list(range(12)))

        self.assertLessEqual(observed_global, 3)
        self.assertLessEqual(observed_by_source["a"], 2)
        self.assertLessEqual(observed_by_source["b"], 2)

    def test_queue_timeout_rejects_excess_work_without_deadlock(self) -> None:
        coordinator = BoundedWorkCoordinator(
            max_global=1,
            max_per_source=1,
            queue_timeout=0.02,
        )
        started = threading.Event()
        release = threading.Event()

        def blocking_operation() -> str:
            started.set()
            release.wait(1)
            return "done"

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                coordinator.run,
                "source-a",
                "first",
                blocking_operation,
            )
            self.assertTrue(started.wait(1))
            with self.assertRaises(WorkCapacityExceeded):
                coordinator.run("source-a", "second", lambda: "unexpected")
            release.set()
            self.assertEqual(first.result(timeout=1), "done")


if __name__ == "__main__":
    unittest.main()
