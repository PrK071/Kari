from __future__ import annotations

import unittest

from backend.config import ConfigurationError, load_settings


class SettingsTests(unittest.TestCase):
    def test_development_defaults_preserve_desktop_runtime(self) -> None:
        settings = load_settings({})

        self.assertEqual(settings.environment, "development")
        self.assertEqual(settings.runtime, "desktop")
        self.assertEqual(
            settings.allowed_origins,
            ("http://localhost:5173", "http://127.0.0.1:5173"),
        )

    def test_production_defaults_to_web_and_requires_explicit_urls(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings({"KARI_ENV": "production"})

        settings = load_settings(
            {
                "KARI_ENV": "production",
                "KARI_BACKEND_URL": "https://api.example.test",
                "KARI_FRONTEND_URL": "https://kari.example.test",
                "KARI_ALLOWED_ORIGINS": "https://kari.example.test",
            }
        )

        self.assertEqual(settings.runtime, "web")
        self.assertEqual(settings.allowed_origins, ("https://kari.example.test",))

    def test_production_rejects_http_and_wildcard_origins(self) -> None:
        base = {
            "KARI_ENV": "production",
            "KARI_BACKEND_URL": "https://api.example.test",
            "KARI_FRONTEND_URL": "https://kari.example.test",
        }
        with self.assertRaises(ConfigurationError):
            load_settings({**base, "KARI_ALLOWED_ORIGINS": "*"})
        with self.assertRaises(ConfigurationError):
            load_settings({**base, "KARI_ALLOWED_ORIGINS": "http://kari.example.test"})


if __name__ == "__main__":
    unittest.main()
