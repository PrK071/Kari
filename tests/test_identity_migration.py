from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.persistence import build_repositories, token_digest
from backend.persistence.models import Base, OAuthAccountModel, SessionModel
from tools.migrate_identity import migrate_identity_data


class IdentityMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "legacy"
        self.source.mkdir()
        database_path = self.root / "identity.sqlite"
        self.repositories = build_repositories(
            backend="postgres",
            database_url=f"sqlite:///{database_path.as_posix()}",
            secret_key="test-migration-secret-that-is-long-enough",
            users_path=lambda: Path("unused-users.json"),
            profiles_path=lambda: Path("unused-profiles.json"),
            sessions_path=lambda: Path("unused-sessions.json"),
        )
        Base.metadata.create_all(self.repositories.engine)

    def tearDown(self) -> None:
        self.repositories.engine.dispose()
        self.temp_dir.cleanup()

    def _write_fixture(self, *, session_profile: str = "profile-a") -> dict[str, bytes]:
        now = time.time()
        payloads = {
            "profiles.json": {
                "profile-a": {
                    "id": "profile-a",
                    "display_name": "Alice",
                    "favorites": [{"id": "favorite-a", "title": "Favorite"}],
                    "library": [],
                    "links": {"anilist": {"id": "42", "name": "Alice", "linked_at": now}},
                    "_tokens": {"anilist": {"access_token": "oauth-secret"}},
                    "created_at": now,
                    "updated_at": now,
                }
            },
            "users.json": {
                "alice": {
                    "username": "Alice",
                    "profile_id": "profile-a",
                    "salt": "00" * 16,
                    "password_hash": "legacy-password-hash",
                    "created_at": now,
                }
            },
            "tokens.json": {
                "legacy-raw-session-token": {
                    "profile_id": session_profile,
                    "username": "Alice",
                    "expires": now + 3600,
                }
            },
        }
        original: dict[str, bytes] = {}
        for name, payload in payloads.items():
            path = self.source / name
            path.write_text(json.dumps(payload), encoding="utf-8")
            original[name] = path.read_bytes()
        return original

    def test_migration_is_idempotent_verified_and_keeps_source(self) -> None:
        original = self._write_fixture()

        first = migrate_identity_data(self.source, self.repositories)
        second = migrate_identity_data(self.source, self.repositories)

        for report in (first, second):
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(report.users_found, 1)
            self.assertEqual(report.users_migrated, 1)
            self.assertEqual(report.profiles_found, 1)
            self.assertEqual(report.profiles_migrated, 1)
            self.assertEqual(report.sessions_found, 1)
            self.assertEqual(report.sessions_migrated, 1)
            self.assertTrue(Path(report.backup_dir).is_dir())
        for name, content in original.items():
            self.assertEqual((self.source / name).read_bytes(), content)
            self.assertEqual((Path(first.backup_dir) / name).read_bytes(), content)

        with Session(self.repositories.engine) as database:
            session = database.scalar(select(SessionModel))
            oauth = database.scalar(select(OAuthAccountModel))
            self.assertEqual(session.token_digest, token_digest("legacy-raw-session-token"))
            self.assertNotIn("legacy-raw-session-token", session.token_digest)
            self.assertNotIn("oauth-secret", oauth.token_ciphertext)

    def test_partial_failure_is_reported_without_changing_source(self) -> None:
        original = self._write_fixture(session_profile="missing-profile")

        report = migrate_identity_data(self.source, self.repositories)

        self.assertFalse(report.ok)
        self.assertEqual(report.profiles_migrated, 1)
        self.assertEqual(report.users_migrated, 1)
        self.assertEqual(report.sessions_migrated, 0)
        self.assertTrue(any(error.startswith("session[1]") for error in report.errors))
        for name, content in original.items():
            self.assertEqual((self.source / name).read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
