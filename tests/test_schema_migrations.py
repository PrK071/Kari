from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class SchemaMigrationTests(unittest.TestCase):
    def test_identity_schema_upgrades_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "migration.sqlite"
            database_url = f"sqlite:///{database_path.as_posix()}"
            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))

            with patch.dict(os.environ, {"DATABASE_URL": database_url}):
                command.upgrade(config, "head")

                engine = create_engine(database_url)
                try:
                    tables = set(inspect(engine).get_table_names())
                finally:
                    engine.dispose()
                self.assertTrue(
                    {
                        "alembic_version",
                        "users",
                        "sessions",
                        "profiles",
                        "profile_favorites",
                        "profile_library",
                        "oauth_accounts",
                    }.issubset(tables)
                )

                command.downgrade(config, "base")
                engine = create_engine(database_url)
                try:
                    remaining = set(inspect(engine).get_table_names())
                finally:
                    engine.dispose()
                self.assertNotIn("users", remaining)
                self.assertNotIn("profiles", remaining)


if __name__ == "__main__":
    unittest.main()
