from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend import main


class CorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_development_origin_can_preflight_authorization(self) -> None:
        response = self.client.options(
            "/api/auth/me",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,x-request-id",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://localhost:5173",
        )
        self.assertIn(
            "X-Request-ID",
            response.headers.get("access-control-allow-headers", ""),
        )
        self.assertNotEqual(response.headers.get("access-control-allow-origin"), "*")

        actual = self.client.get(
            "/health",
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertIn(
            "X-Request-ID",
            actual.headers.get("access-control-expose-headers", ""),
        )

    def test_unlisted_origin_is_rejected(self) -> None:
        response = self.client.options(
            "/api/auth/me",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)


if __name__ == "__main__":
    unittest.main()
