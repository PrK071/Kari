from __future__ import annotations

import json
import time
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

    def test_search_metrics_do_not_log_raw_query(self) -> None:
        local_item = {
            "id": "local-work",
            "title": "Private Search Title",
            "source": "MangaDex",
            "provider": "mangadex",
            "source_url": "https://mangadex.org/title/local-work",
            "chapter_count": 1,
            "cover_url": "https://example.test/cover.jpg",
        }
        with (
            patch("backend.main._search_sources", return_value=[]),
            patch("backend.main._catalog_search_matches", return_value=[local_item]),
            patch("backend.main._fast_curated_catalog_items", return_value=[]),
            patch("backend.main._share_search_covers_by_title"),
            patch("backend.main._recover_missing_search_covers"),
            patch("backend.main._apply_curated_source_overrides", side_effect=lambda items, _query: items),
            patch("backend.main._log_search_metrics") as log_metrics,
        ):
            result = main._search_mangas("Private Search Title", limit=8)

        metrics = log_metrics.call_args.args[0]
        self.assertEqual(result["total"], 1)
        self.assertEqual(metrics["cache"], "miss")
        self.assertEqual(metrics["catalog_backend"], "memory_or_json")
        self.assertIsNone(metrics["postgres_access_ms"])
        self.assertIsNotNone(metrics["time_to_first_result_ms"])
        self.assertNotIn("private search title", json.dumps(metrics).lower())

    def test_provider_timeout_is_measured_individually(self) -> None:
        telemetry: list[dict] = []

        def slow_source(_source: str, _query: str, _limit: int) -> list[dict]:
            time.sleep(0.03)
            return []

        started_at = time.perf_counter()
        with patch("backend.main._search_source", side_effect=slow_source):
            items, errors = main._search_sources_with_timeout(
                ["mangadex"],
                "slow title",
                8,
                timeout=0.005,
                telemetry=telemetry,
                request_started_at=started_at,
            )

        self.assertEqual(items, [])
        self.assertEqual(errors, ["MangaDex: timeout"])
        self.assertEqual(len(telemetry), 1)
        self.assertTrue(telemetry[0]["timeout"])
        self.assertGreaterEqual(telemetry[0]["provider_duration_ms"], 4)


if __name__ == "__main__":
    unittest.main()
