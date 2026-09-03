from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.persistence import (
    JsonProfileRepository,
    JsonSessionRepository,
    JsonUserRepository,
)


class JsonRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_user_repository_finds_login_and_profile(self) -> None:
        path = self.root / "users.json"
        repository = JsonUserRepository(lambda: path)
        user = {"username": "Alice", "profile_id": "profile-a"}

        repository.save("alice", user)

        self.assertEqual(repository.get("alice"), user)
        self.assertEqual(repository.get_by_profile_id("profile-a"), user)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"alice": user})

    def test_profile_repository_preserves_legacy_shape(self) -> None:
        path = self.root / "profiles.json"
        repository = JsonProfileRepository(lambda: path)
        profile = {
            "id": "profile-a",
            "display_name": "Alice",
            "favorites": [{"id": "manga-a", "title": "Manga A"}],
            "_tokens": {"anilist": {"access_token": "secret"}},
        }

        repository.save(profile)

        self.assertEqual(repository.get("profile-a"), profile)
        self.assertEqual(repository.all(), {"profile-a": profile})

    def test_session_repository_revokes_and_purges(self) -> None:
        path = self.root / "tokens.json"
        repository = JsonSessionRepository(lambda: path)
        repository.save("expired", {"profile_id": "a", "expires": 10})
        repository.save("active", {"profile_id": "b", "expires": 30})

        self.assertEqual(repository.purge_expired(20), 1)
        self.assertIsNone(repository.get("expired"))
        self.assertEqual(repository.get("active")["profile_id"], "b")
        self.assertTrue(repository.revoke("active"))
        self.assertFalse(repository.revoke("active"))

    def test_profile_requires_stable_id(self) -> None:
        repository = JsonProfileRepository(lambda: self.root / "profiles.json")
        with self.assertRaises(ValueError):
            repository.save({"display_name": "missing id"})


if __name__ == "__main__":
    unittest.main()
