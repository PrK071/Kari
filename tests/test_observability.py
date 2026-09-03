from __future__ import annotations

import logging
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend import main


class ObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def test_health_is_minimal_and_has_sanitized_access_log(self) -> None:
        secret = "bearer-secret-that-must-not-be-logged"
        with self.assertLogs("mangatemp", level=logging.INFO) as captured:
            response = self.client.get(
                "/health?password=also-secret",
                headers={"Authorization": f"Bearer {secret}", "X-Request-ID": "test-123"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["X-Request-ID"], "test-123")
        log_output = "\n".join(captured.output)
        self.assertIn("route=/health", log_output)
        self.assertNotIn(secret, log_output)
        self.assertNotIn("also-secret", log_output)
        self.assertNotIn("Authorization", log_output)

    def test_invalid_request_id_is_not_reflected(self) -> None:
        response = self.client.get("/health", headers={"X-Request-ID": "bad id\nvalue"})

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers["X-Request-ID"], "bad id\nvalue")
        self.assertRegex(response.headers["X-Request-ID"], r"^[a-f0-9]{32}$")

    def test_ready_reports_only_dependency_state(self) -> None:
        ready_repositories = Mock()
        ready_repositories.ready.return_value = True
        with patch.object(main, "repositories", ready_repositories):
            response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

        unavailable_repositories = Mock()
        unavailable_repositories.ready.return_value = False
        with patch.object(main, "repositories", unavailable_repositories):
            response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})


if __name__ == "__main__":
    unittest.main()
