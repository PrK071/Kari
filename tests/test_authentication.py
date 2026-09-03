from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend import main
from backend.config import load_settings


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.original_paths = (
            main.USERS_STORE_PATH,
            main.AUTH_TOKENS_PATH,
            main.PROFILES_STORE_PATH,
        )
        self.original_settings = main.settings
        main.USERS_STORE_PATH = data_dir / "users.json"
        main.AUTH_TOKENS_PATH = data_dir / "tokens.json"
        main.PROFILES_STORE_PATH = data_dir / "profiles.json"
        main.settings = load_settings({"KARI_RUNTIME": "web", "KARI_SESSION_TTL_HOURS": "1"})
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        (
            main.USERS_STORE_PATH,
            main.AUTH_TOKENS_PATH,
            main.PROFILES_STORE_PATH,
        ) = self.original_paths
        main.settings = self.original_settings
        self.temp_dir.cleanup()

    def _register(self, username: str = "alice", password: str = "correct horse battery staple"):
        return self.client.post(
            "/api/auth/register",
            json={"username": username, "password": password},
        )

    @staticmethod
    def _bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_registration_requires_long_password_and_uses_argon2id(self) -> None:
        weak = self._register(password="too-short")
        self.assertEqual(weak.status_code, 422)

        registered = self._register()
        self.assertEqual(registered.status_code, 200, registered.text)
        user = main.user_repository.get("alice")
        self.assertEqual(user["password_algorithm"], "argon2id")
        self.assertTrue(user["password_hash"].startswith("$argon2id$"))
        self.assertNotIn("salt", user)
        self.assertNotIn("password", registered.text.lower())

    def test_login_wrong_password_multiple_sessions_and_logout(self) -> None:
        registered = self._register().json()
        wrong = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "this is the wrong password"},
        )
        self.assertEqual(wrong.status_code, 401)

        login = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "correct horse battery staple"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertNotEqual(login.json()["token"], registered["token"])
        self.assertEqual(len(main.session_repository.all()), 2)

        headers = self._bearer(login.json()["token"])
        self.assertEqual(self.client.post("/api/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/logout", headers=headers).status_code, 401)

    def test_expired_and_invalid_sessions_are_rejected(self) -> None:
        registered = self._register().json()
        token = registered["token"]
        main.session_repository.save(
            token,
            {
                "profile_id": registered["profile"]["id"],
                "username": "alice",
                "created_at": time.time() - 7200,
                "expires": time.time() - 3600,
            },
        )

        self.assertEqual(self.client.get("/api/auth/me", headers=self._bearer(token)).status_code, 401)
        self.assertIsNone(main.session_repository.get(token))
        self.assertEqual(
            self.client.get("/api/auth/me", headers=self._bearer("invalid")).status_code,
            401,
        )

    def test_legacy_pbkdf2_hash_is_rehashed_after_login(self) -> None:
        password = "legacy password is still valid"
        salt = "01" * 16
        now = time.time()
        main.profile_repository.save(
            {
                "id": "legacy-profile",
                "display_name": "Legacy",
                "favorites": [],
                "library": [],
                "created_at": now,
                "updated_at": now,
            }
        )
        main.user_repository.save(
            "legacy",
            {
                "username": "Legacy",
                "profile_id": "legacy-profile",
                "salt": salt,
                "password_hash": main._hash_password(password, salt),
                "created_at": now,
            },
        )

        response = self.client.post(
            "/api/auth/login",
            json={"username": "legacy", "password": password},
        )

        self.assertEqual(response.status_code, 200, response.text)
        user = main.user_repository.get("legacy")
        self.assertEqual(user["password_algorithm"], "argon2id")
        self.assertTrue(user["password_hash"].startswith("$argon2id$"))
        self.assertNotIn("salt", user)


if __name__ == "__main__":
    unittest.main()
