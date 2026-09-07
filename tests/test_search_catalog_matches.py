from __future__ import annotations

import unittest
from unittest.mock import patch

from backend import main


class SearchCatalogMatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_catalog_cache = main.catalog_cache
        main.search_cache.clear()

    def tearDown(self) -> None:
        main.catalog_cache = self.original_catalog_cache
        main.search_cache.clear()

    def test_search_keeps_catalog_card_when_source_uses_alternative_title(self) -> None:
        catalog_item = {
            "id": "catalog-work",
            "title": "In Another World With My Smartphone",
            "source": "MangaKatana",
            "provider": "mangakatana",
            "source_url": "https://mangakatana.com/manga/in-another-world-with-my-smartphone.123",
            "alternative_titles": [],
            "chapter_count": 10,
            "cover_url": "https://example.test/catalog-cover.jpg",
        }
        remote_item = {
            "id": "remote-work",
            "title": "Isekai wa Smartphone to Tomo ni",
            "source": "MangaDex",
            "provider": "mangadex",
            "source_url": "https://mangadex.org/title/remote-work",
            "alternative_titles": ["In Another World With My Smartphone"],
            "chapter_count": 20,
            "cover_url": "https://example.test/remote-cover.jpg",
        }
        main.catalog_cache = main.CacheEntry(
            0,
            {"items": [catalog_item], "sections": []},
        )

        with (
            patch("backend.main._search_sources", return_value=["mangadex"]),
            patch("backend.main._search_sources_with_timeout", return_value=([remote_item], [])),
            patch("backend.main._fast_curated_catalog_items", return_value=[]),
            patch("backend.main._share_search_covers_by_title"),
            patch("backend.main._recover_missing_search_covers"),
            patch("backend.main._apply_curated_source_overrides", side_effect=lambda items, _query: items),
        ):
            result = main._search_mangas(catalog_item["title"], limit=8)

        self.assertEqual(result["items"][0]["source_url"], catalog_item["source_url"])
        self.assertIn(catalog_item["source_url"], {
            item["source_url"] for item in result["items"]
        })


if __name__ == "__main__":
    unittest.main()
