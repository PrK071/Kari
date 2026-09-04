from __future__ import annotations

import unittest

from backend.config import ConfigurationError, load_settings


class SettingsTests(unittest.TestCase):
    def test_development_defaults_preserve_desktop_runtime(self) -> None:
        settings = load_settings({})

        self.assertEqual(settings.environment, "development")
        self.assertEqual(settings.runtime, "desktop")
        self.assertEqual(settings.persistence_backend, "json")
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
                "DATABASE_URL": "postgresql+psycopg://kari:password@db/kari",
                "KARI_SECRET_KEY": "a" * 32,
            }
        )

        self.assertEqual(settings.runtime, "web")
        self.assertEqual(settings.persistence_backend, "postgres")
        self.assertEqual(settings.allowed_origins, ("https://kari.example.test",))

    def test_production_rejects_http_and_wildcard_origins(self) -> None:
        base = {
            "KARI_ENV": "production",
            "KARI_BACKEND_URL": "https://api.example.test",
            "KARI_FRONTEND_URL": "https://kari.example.test",
            "DATABASE_URL": "postgresql+psycopg://kari:password@db/kari",
            "KARI_SECRET_KEY": "a" * 32,
        }
        with self.assertRaises(ConfigurationError):
            load_settings({**base, "KARI_ALLOWED_ORIGINS": "*"})
        with self.assertRaises(ConfigurationError):
            load_settings({**base, "KARI_ALLOWED_ORIGINS": "http://kari.example.test"})

    def test_postgres_requires_database_and_encryption_secret(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings({"KARI_PERSISTENCE_BACKEND": "postgres"})
        with self.assertRaises(ConfigurationError):
            load_settings(
                {
                    "KARI_PERSISTENCE_BACKEND": "postgres",
                    "DATABASE_URL": "sqlite:///test.db",
                    "KARI_SECRET_KEY": "short",
                }
            )

    def test_session_ttl_and_rate_limit_backend_are_validated(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings({"KARI_SESSION_TTL_HOURS": "0"})
        with self.assertRaises(ConfigurationError):
            load_settings({"KARI_SESSION_TTL_HOURS": "not-a-number"})
        with self.assertRaises(ConfigurationError):
            load_settings({"KARI_RATE_LIMIT_BACKEND": "redis"})
        with self.assertRaises(ConfigurationError):
            load_settings({"KARI_LOG_LEVEL": "VERBOSE"})
        with self.assertRaises(ConfigurationError):
            load_settings({"KARI_SCRAPER_MAX_CONCURRENCY": "0"})
        with self.assertRaises(ConfigurationError):
            load_settings({"KARI_BACKGROUND_MAX_CONCURRENCY": "17"})
        with self.assertRaises(ConfigurationError):
            load_settings(
                {
                    "KARI_SCRAPER_MAX_CONCURRENCY": "2",
                    "KARI_SCRAPER_MAX_PER_SOURCE": "3",
                }
            )


if __name__ == "__main__":
    unittest.main()
