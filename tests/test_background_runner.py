from __future__ import annotations

import threading
import unittest

from backend.background import BackgroundTaskRunner


class BackgroundTaskRunnerTests(unittest.TestCase):
    def test_bounds_active_threads_and_recovers_capacity(self) -> None:
        runner = BackgroundTaskRunner(max_concurrency=2)
        release = threading.Event()
        started = [threading.Event(), threading.Event()]

        def block(index: int) -> None:
            started[index].set()
            release.wait(2)

        self.assertTrue(runner.submit("one", block, 0))
        self.assertTrue(runner.submit("two", block, 1))
        self.assertTrue(started[0].wait(1))
        self.assertTrue(started[1].wait(1))
        self.assertEqual(runner.active_count(), 2)
        self.assertFalse(runner.submit("three", lambda: None))
        self.assertTrue(runner.submit("one", lambda: self.fail("duplicate ran")))

        release.set()
        for _ in range(100):
            if runner.active_count() == 0:
                break
            threading.Event().wait(0.01)
        self.assertEqual(runner.active_count(), 0)
        completed = threading.Event()
        self.assertTrue(runner.submit("three", completed.set))
        self.assertTrue(completed.wait(1))

    def test_releases_slot_when_task_raises(self) -> None:
        runner = BackgroundTaskRunner(max_concurrency=1)
        completed = threading.Event()

        def fail() -> None:
            completed.set()
            raise RuntimeError("expected")

        self.assertTrue(runner.submit("failure", fail))
        self.assertTrue(completed.wait(1))
        for _ in range(100):
            if runner.active_count() == 0:
                break
            threading.Event().wait(0.01)
        self.assertEqual(runner.active_count(), 0)
        self.assertTrue(runner.submit("after-failure", lambda: None))


if __name__ == "__main__":
    unittest.main()
