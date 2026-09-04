from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.persistence import build_repositories
from backend.persistence.models import Base


POSTGRES_URL = os.environ.get("KARI_TEST_POSTGRES_URL", "").strip()


@unittest.skipUnless(
    POSTGRES_URL,
    "KARI_TEST_POSTGRES_URL nao configurada; executar este gate no staging.",
)
class RealPostgresIntegrationTests(unittest.TestCase):
    def test_repositories_round_trip_in_isolated_schema(self) -> None:
        schema = f"kari_test_{uuid4().hex}"
        admin_engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
        repositories = None
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))

            isolated_url = make_url(POSTGRES_URL).update_query_dict(
                {"options": f"-csearch_path={schema}"}
            )
            unused = lambda: Path("unused.json")
            repositories = build_repositories(
                backend="postgres",
                database_url=isolated_url.render_as_string(hide_password=False),
                secret_key="staging-test-secret-that-is-long-enough",
                users_path=unused,
                profiles_path=unused,
                sessions_path=unused,
            )
            Base.metadata.create_all(repositories.engine)
            repositories.profiles.save(
                {
                    "id": "profile-test",
                    "display_name": "Staging",
                    "favorites": [],
                    "library": [],
                }
            )
            repositories.users.save(
                "staging",
                {
                    "username": "Staging",
                    "profile_id": "profile-test",
                    "password_hash": "$argon2id$test",
                    "password_algorithm": "argon2id",
                },
            )
            repositories.sessions.save(
                "raw-token-not-persisted",
                {
                    "profile_id": "profile-test",
                    "username": "Staging",
                    "expires": 4_102_444_800,
                },
            )

            self.assertTrue(repositories.ready())
            self.assertEqual(
                repositories.sessions.get("raw-token-not-persisted")["profile_id"],
                "profile-test",
            )
            self.assertNotIn("raw-token-not-persisted", repositories.sessions.all())
        finally:
            if repositories and repositories.engine:
                repositories.engine.dispose()
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            admin_engine.dispose()


if __name__ == "__main__":
    unittest.main()
