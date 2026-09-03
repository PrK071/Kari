from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.config import load_settings
from backend.rate_limit import MemoryRateLimitBackend, RateLimiter, RateLimitPolicy


class MemoryRateLimitTests(unittest.TestCase):
    def test_allows_below_limit_and_rejects_above_limit(self) -> None:
        backend = MemoryRateLimitBackend()
        policy = RateLimitPolicy(limit=2, window_seconds=60)

        self.assertTrue(backend.consume("login:ip:a", policy, now=10).allowed)
        self.assertTrue(backend.consume("login:ip:a", policy, now=20).allowed)
        rejected = backend.consume("login:ip:a", policy, now=30)

        self.assertFalse(rejected.allowed)
        self.assertEqual(rejected.retry_after, 40)
        self.assertTrue(backend.consume("login:ip:a", policy, now=71).allowed)

    def test_users_have_independent_buckets(self) -> None:
        limiter = RateLimiter(MemoryRateLimitBackend())
        policy = RateLimitPolicy(limit=1, window_seconds=60)

        self.assertTrue(limiter.check("sync", policy, {"user": "alice"}).allowed)
        self.assertFalse(limiter.check("sync", policy, {"user": "alice"}).allowed)
        self.assertTrue(limiter.check("sync", policy, {"user": "bob"}).allowed)


class EndpointRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original = (
            main.USERS_STORE_PATH,
            main.AUTH_TOKENS_PATH,
            main.PROFILES_STORE_PATH,
            main.settings,
            main.rate_limiter,
            main.LOGIN_RATE_LIMIT,
            main.SEARCH_RATE_LIMIT,
        )
        main.USERS_STORE_PATH = root / "users.json"
        main.AUTH_TOKENS_PATH = root / "tokens.json"
        main.PROFILES_STORE_PATH = root / "profiles.json"
        main.settings = load_settings({"KARI_RUNTIME": "web"})
        main.rate_limiter = RateLimiter(MemoryRateLimitBackend())
        main.LOGIN_RATE_LIMIT = RateLimitPolicy(limit=2, window_seconds=60)
        main.SEARCH_RATE_LIMIT = RateLimitPolicy(limit=2, window_seconds=60)
        self.client = TestClient(main.app)
        registered = self.client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "correct horse battery staple"},
        )
        self.assertEqual(registered.status_code, 200, registered.text)

    def tearDown(self) -> None:
        self.client.close()
        (
            main.USERS_STORE_PATH,
            main.AUTH_TOKENS_PATH,
            main.PROFILES_STORE_PATH,
            main.settings,
            main.rate_limiter,
            main.LOGIN_RATE_LIMIT,
            main.SEARCH_RATE_LIMIT,
        ) = self.original
        self.temp_dir.cleanup()

    def test_login_returns_429_with_retry_after(self) -> None:
        for _ in range(2):
            response = self.client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "wrong password"},
            )
            self.assertEqual(response.status_code, 401)

        limited = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong password"},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertGreaterEqual(int(limited.headers["Retry-After"]), 1)

    @patch("backend.main._build_search_payload")
    def test_search_is_limited_before_scrapers_run(self, build_payload) -> None:
        build_payload.return_value = {
            "items": [],
            "sections": [],
            "total": 0,
            "limit": 10,
            "offset": 0,
            "sources": [],
            "errors": [],
            "cached": False,
        }
        for _ in range(2):
            self.assertEqual(self.client.get("/api/search?q=test&limit=10").status_code, 200)

        limited = self.client.get("/api/search?q=test&limit=10")
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(build_payload.call_count, 2)


if __name__ == "__main__":
    unittest.main()
