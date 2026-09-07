from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.migrate_catalog import collect_catalog_items


class CatalogMigrationToolTests(unittest.TestCase):
    def test_collects_snapshot_and_skips_dataset_without_work_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "catalog.json"
            snapshot.write_text(
                json.dumps({
                    "items": [{
                        "title": "Vinland Saga",
                        "provider": "mangadex",
                        "source_url": "https://mangadex.org/title/vinland",
                        "cover_url": "https://example.test/vinland.jpg",
                    }],
                    "sections": [],
                }),
                encoding="utf-8",
            )
            database_path = root / "mangas.db"
            with closing(sqlite3.connect(database_path)) as database:
                database.execute(
                    """
                    CREATE TABLE pages (
                        manga_name TEXT, manga_slug TEXT, chapter TEXT,
                        page_number INTEGER, url_image TEXT, source TEXT,
                        scraped_at TEXT
                    )
                    """
                )
                database.commit()
                database.execute(
                    "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "Offline only", "offline-only", "1", 1,
                        "https://images.example.test/1.jpg", "ExampleSource",
                        "2026-09-07T00:00:00Z",
                    ),
                )
                database.commit()

            items, report = collect_catalog_items(snapshot, database_path)

        self.assertEqual(report.snapshot_found, 1)
        self.assertEqual(report.dataset_groups_found, 1)
        self.assertEqual(report.dataset_groups_skipped, 1)
        self.assertIn("Vinland Saga", {item["title"] for item in items})
        self.assertNotIn("Offline only", {item["title"] for item in items})


if __name__ == "__main__":
    unittest.main()
