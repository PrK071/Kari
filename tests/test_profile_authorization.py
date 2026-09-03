from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend import main
from backend.config import load_settings


class ProfileAuthorizationTests(unittest.TestCase):
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
        main.settings = load_settings({"KARI_RUNTIME": "web"})
        self.client = TestClient(main.app)

        self.alice = self._register("alice", "correct horse battery staple")
        self.bob = self._register("bob", "another correct horse battery staple")

    def tearDown(self) -> None:
        self.client.close()
        (
            main.USERS_STORE_PATH,
            main.AUTH_TOKENS_PATH,
            main.PROFILES_STORE_PATH,
        ) = self.original_paths
        main.settings = self.original_settings
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

    def test_owner_can_read_own_profile(self) -> None:
        response = self.client.get(
            f"/api/profiles/{self.alice['profile']['id']}",
            headers=self._bearer(self.alice["token"]),
        )
        self.assertEqual(response.status_code, 200)

    def test_authenticated_user_cannot_read_another_profile(self) -> None:
        profile_id = self.bob["profile"]["id"]
        cases = (
            ("GET", f"/api/profiles/{profile_id}", None),
            ("PUT", f"/api/profiles/{profile_id}", {"display_name": "stolen"}),
            ("PUT", f"/api/profiles/{profile_id}/favorites", {"favorites": []}),
            (
                "PUT",
                f"/api/profiles/{profile_id}/library",
                {"item": {"id": "work", "title": "Work"}},
            ),
            (
                "DELETE",
                f"/api/profiles/{profile_id}/library",
                {"item": {"id": "work", "title": "Work"}},
            ),
            ("PUT", f"/api/profiles/{profile_id}/avatar", {}),
            ("PUT", f"/api/profiles/{profile_id}/background", {}),
            ("PUT", f"/api/profiles/{profile_id}/home-background", {}),
            ("POST", f"/api/profiles/{profile_id}/link/anilist", None),
            ("DELETE", f"/api/profiles/{profile_id}/link/anilist", None),
            ("GET", f"/api/profiles/{profile_id}/link/status", None),
            ("POST", f"/api/profiles/{profile_id}/sync/anilist", None),
        )
        for method, path, body in cases:
            with self.subTest(method=method, path=path):
                response = self.client.request(
                    method,
                    path,
                    headers=self._bearer(self.alice["token"]),
                    json=body,
                )
                self.assertEqual(response.status_code, 403, response.text)

    def test_missing_and_invalid_tokens_are_rejected(self) -> None:
        path = f"/api/profiles/{self.alice['profile']['id']}"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(
            self.client.get(path, headers=self._bearer("invalid-token")).status_code,
            401,
        )

    def test_anonymous_profiles_are_limited_to_desktop_runtime(self) -> None:
        create_web = self.client.post(
            "/api/profiles",
            json={"display_name": "guest"},
        )
        self.assertEqual(create_web.status_code, 404)

        main.settings = load_settings({"KARI_RUNTIME": "desktop"})
        profile_id = self.alice["profile"]["id"]
        self.assertEqual(
            self.client.get(f"/api/profiles/{profile_id}").status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
