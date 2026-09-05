from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend import main
from backend.config import load_settings
from backend.media_storage import MediaStorageUnavailable
from backend.persistence import build_repositories


class _FakeStorage:
    writable = True

    def __init__(self, media: dict[tuple[str, str], tuple[bytes, str]] | None = None) -> None:
        self.media = media or {}
        self.fail_reads = False

    def replace(self, profile_id, kind, suffix, content, content_type) -> str:
        self.media[(profile_id, kind)] = (content, content_type)
        return f"/api/profiles/{profile_id}/media/{kind}"

    def delete(self, profile_id, kind) -> None:
        self.media.pop((profile_id, kind), None)

    def read(self, profile_id, kind) -> tuple[bytes, str] | None:
        if self.fail_reads:
            raise MediaStorageUnavailable("Object Storage indisponivel.")
        return self.media.get((profile_id, kind))


class ProfileMediaEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.repositories = build_repositories(
            backend="json",
            database_url="",
            secret_key="endpoint-test-secret-that-is-long-enough",
            users_path=lambda: data_dir / "users.json",
            profiles_path=lambda: data_dir / "profiles.json",
            sessions_path=lambda: data_dir / "tokens.json",
        )
        self.original_repos = (
            main.repositories,
            main.user_repository,
            main.profile_repository,
            main.session_repository,
        )
        self.original_settings = main.settings
        self.original_storage = main.profile_media_storage
        main.repositories = self.repositories
        main.user_repository = self.repositories.users
        main.profile_repository = self.repositories.profiles
        main.session_repository = self.repositories.sessions
        main.settings = load_settings({"KARI_RUNTIME": "web"})
        self.storage = _FakeStorage()
        main.profile_media_storage = self.storage
        self.client = TestClient(main.app)

        self.alice = self._register("media_alice", "correct horse battery staple")
        self.bob = self._register("media_bob", "another correct horse battery staple")
        self.storage.media[
            (self.alice["profile"]["id"], "avatar")
        ] = (b"alice-avatar-bytes", "image/png")

    def tearDown(self) -> None:
        self.client.close()
        (
            main.repositories,
            main.user_repository,
            main.profile_repository,
            main.session_repository,
        ) = self.original_repos
        main.settings = self.original_settings
        main.profile_media_storage = self.original_storage
        self.temp_dir.cleanup()

    def _register(self, username: str, password: str) -> dict:
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_owner_reads_own_media_with_correct_content_type(self) -> None:
        profile_id = self.alice["profile"]["id"]
        response = self.client.get(
            f"/api/profiles/{profile_id}/media/avatar",
            headers=self._bearer(self.alice["token"]),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"alice-avatar-bytes")
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["cache-control"], "private, max-age=300")

    def test_other_user_cannot_read_media(self) -> None:
        profile_id = self.alice["profile"]["id"]
        response = self.client.get(
            f"/api/profiles/{profile_id}/media/avatar",
            headers=self._bearer(self.bob["token"]),
        )
        self.assertEqual(response.status_code, 403, response.text)

        reverse = self.client.get(
            f"/api/profiles/{self.bob['profile']['id']}/media/avatar",
            headers=self._bearer(self.alice["token"]),
        )
        self.assertEqual(reverse.status_code, 403, reverse.text)

    def test_media_requires_authentication(self) -> None:
        response = self.client.get(
            f"/api/profiles/{self.alice['profile']['id']}/media/avatar",
        )
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    def test_unknown_kind_is_404(self) -> None:
        response = self.client.get(
            f"/api/profiles/{self.alice['profile']['id']}/media/cover",
            headers=self._bearer(self.alice["token"]),
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_missing_media_is_404(self) -> None:
        response = self.client.get(
            f"/api/profiles/{self.alice['profile']['id']}/media/background",
            headers=self._bearer(self.alice["token"]),
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_forbidden_content_type_is_not_served(self) -> None:
        profile_id = self.alice["profile"]["id"]
        self.storage.media[(profile_id, "background")] = (b"<html>", "text/html")
        response = self.client.get(
            f"/api/profiles/{profile_id}/media/background",
            headers=self._bearer(self.alice["token"]),
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_storage_failure_is_503_without_secrets(self) -> None:
        self.storage.fail_reads = True
        response = self.client.get(
            f"/api/profiles/{self.alice['profile']['id']}/media/avatar",
            headers=self._bearer(self.alice["token"]),
        )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("Object Storage indisponivel.", response.text)
        self.assertNotIn("access", response.text.lower())
        self.assertNotIn("secret", response.text.lower())
        self.assertNotIn("npg_", response.text)


if __name__ == "__main__":
    unittest.main()
