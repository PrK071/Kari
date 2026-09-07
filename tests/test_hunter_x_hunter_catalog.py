from __future__ import annotations

import unittest
from unittest.mock import patch

from backend import main


class HunterXHunterCatalogTests(unittest.TestCase):
    def test_hunter_x_hunter_is_curated_with_verified_mangalivre_source(self) -> None:
        entry = next(
            item for item in main.CURATED_CATALOG
            if item["title"] == "HUNTER x HUNTER"
        )

        self.assertEqual(
            entry["url"],
            "https://mangalivre.blog/manga/hunter-x-hunter/",
        )
        self.assertEqual(entry["provider"], "mangalivre")
        self.assertEqual(
            main._confirmed_source_override("Hunter x Hunter")["source_url"],
            entry["url"],
        )

    def test_curated_work_survives_remote_catalog_refresh(self) -> None:
        hunter = main._normalize_manga_item(
            next(
                item for item in main.CURATED_CATALOG
                if item["title"] == "HUNTER x HUNTER"
            ),
            section="Aventura",
        )
        remote = {
            "id": "remote-work",
            "title": "Remote Work",
            "source_url": "https://mangadex.org/title/remote-work",
            "provider": "mangadex",
            "source": "MangaDex",
            "section": "Aventura",
        }

        with patch("backend.main._fast_curated_catalog_items", return_value=[hunter]):
            items, sections = main._merge_curated_catalog(
                [remote],
                [{"title": "Aventura", "items": [remote]}],
            )

        self.assertEqual(items[0]["source_url"], hunter["source_url"])
        self.assertEqual(sections[0]["items"][0]["source_url"], hunter["source_url"])


if __name__ == "__main__":
    unittest.main()
