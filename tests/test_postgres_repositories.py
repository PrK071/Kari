from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import main
from backend.config import load_settings
from backend.persistence import build_repositories, token_digest
from backend.persistence.models import Base, OAuthAccountModel, SessionModel


class PostgresRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "identity.sqlite"
        self.repositories = build_repositories(
            backend="postgres",
            database_url=f"sqlite:///{database_path.as_posix()}",
            secret_key="test-encryption-secret-that-is-long-enough",
            users_path=lambda: Path("unused-users.json"),
            profiles_path=lambda: Path("unused-profiles.json"),
            sessions_path=lambda: Path("unused-sessions.json"),
        )
        Base.metadata.create_all(self.repositories.engine)
        self.original = (
            main.repositories,
            main.user_repository,
            main.profile_repository,
            main.session_repository,
            main.settings,
        )
        main.repositories = self.repositories
        main.user_repository = self.repositories.users
        main.profile_repository = self.repositories.profiles
        main.session_repository = self.repositories.sessions
        main.settings = load_settings({"KARI_RUNTIME": "web"})
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        (
            main.repositories,
            main.user_repository,
            main.profile_repository,
            main.session_repository,
            main.settings,
        ) = self.original
        self.repositories.engine.dispose()
        self.temp_dir.cleanup()

    def test_application_auth_flow_uses_relational_repositories(self) -> None:
        register = self.client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "correct horse battery staple"},
        )
        self.assertEqual(register.status_code, 200, register.text)
        token = register.json()["token"]
        profile_id = register.json()["profile"]["id"]

        own_profile = self.client.get(
            f"/api/profiles/{profile_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(own_profile.status_code, 200, own_profile.text)

        with Session(self.repositories.engine) as database:
            stored = database.scalar(select(SessionModel))
            self.assertEqual(stored.token_digest, token_digest(token))
            self.assertNotEqual(stored.token_digest, token)

    def test_profile_round_trip_normalizes_lists_and_encrypts_oauth_tokens(self) -> None:
        now = time.time()
        profile = {
            "id": "profile-a",
            "display_name": "Alice",
            "favorites": [{"id": "favorite-a", "title": "Favorite"}],
            "library": [
                {
                    "id": "library-a",
                    "title": "Library",
                    "status": "CURRENT",
                    "score": 8.5,
                    "review": "good",
                    "updated_at": now,
                }
            ],
            "links": {
                "anilist": {
                    "id": "42",
                    "name": "alice",
                    "linked_at": now,
                }
            },
            "_tokens": {
                "anilist": {
                    "access_token": "oauth-secret-value",
                    "refresh_token": "refresh-secret-value",
                }
            },
            "created_at": now,
            "updated_at": now,
        }

        self.repositories.profiles.save(profile)
        loaded = self.repositories.profiles.get("profile-a")

        self.assertEqual(loaded["favorites"][0]["id"], "favorite-a")
        self.assertEqual(loaded["library"][0]["status"], "CURRENT")
        self.assertEqual(
            loaded["_tokens"]["anilist"]["access_token"],
            "oauth-secret-value",
        )
        with Session(self.repositories.engine) as database:
            ciphertext = database.scalar(select(OAuthAccountModel.token_ciphertext))
            self.assertNotIn("oauth-secret-value", ciphertext)

    def test_logout_revokes_database_session(self) -> None:
        register = self.client.post(
            "/api/auth/register",
            json={"username": "bob", "password": "another long safe password"},
        ).json()
        headers = {"Authorization": f"Bearer {register['token']}"}

        self.assertEqual(self.client.post("/api/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
