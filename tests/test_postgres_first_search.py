from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks

from backend import main


def manga(title: str = "Hunter x Hunter", provider: str = "mangalivre") -> dict:
    slug = title.casefold().replace(" ", "-")
    return {
        "id": f"{provider}:{slug}",
        "title": title,
        "provider": provider,
        "source": main._source_label(provider),
        "source_url": f"https://example.test/{provider}/{slug}",
        "cover_url": "https://example.test/cover.jpg",
        "chapter_count": 420,
        "genres": ["Aventura"],
        "catalog_home_ready": True,
    }


class FakeCatalogRepository:
    def __init__(self, items: list[dict] | None = None) -> None:
        self.items = list(items or [])
        self.upsert_calls = 0

    def search(self, query: str, limit: int) -> list[dict]:
        normalized = main.normalize_match_text(query)
        return [
            dict(item)
            for item in self.items
            if normalized in main.normalize_match_text(item["title"])
        ][:limit]

    def upsert_many(self, items: list[dict]) -> int:
        self.upsert_calls += 1
        known = {item["source_url"] for item in self.items}
        for item in items:
            if item["source_url"] not in known:
                self.items.append(dict(item))
                known.add(item["source_url"])
        return len(items)

    def list_home(self, limit: int) -> list[dict]:
        return [dict(item) for item in self.items if item.get("catalog_home_ready")][:limit]


class PostgresFirstSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_repository = main.catalog_repository
        self.original_catalog_cache = main.catalog_cache
        main.search_cache.clear()
        main.catalog_cache = None

    def tearDown(self) -> None:
        main.catalog_repository = self.original_repository
        main.catalog_cache = self.original_catalog_cache
        main.search_cache.clear()

    def test_known_work_returns_without_foreground_provider(self) -> None:
        main.catalog_repository = FakeCatalogRepository([manga()])
        with (
            patch("backend.main._foreground_external_search") as external,
            patch("backend.main._schedule_search_refresh", return_value=True),
        ):
            started = time.perf_counter()
            result = main._search_mangas("HUNTER × HUNTER", 8)
            duration = time.perf_counter() - started

        self.assertEqual(result["items"][0]["title"], "Hunter x Hunter")
        self.assertLess(duration, 0.1)
        external.assert_not_called()

    def test_http_flow_defers_refresh_until_after_response(self) -> None:
        main.catalog_repository = FakeCatalogRepository([manga()])
        with patch("backend.main._schedule_search_refresh") as schedule:
            result = main._search_mangas(
                "hunter x hunter",
                8,
                defer_refresh=True,
            )

        self.assertIn("_refresh_deferred", result)
        schedule.assert_not_called()

    def test_web_search_does_not_scan_desktop_libraries(self) -> None:
        main.catalog_repository = FakeCatalogRepository([manga()])
        with (
            patch("backend.main.settings", SimpleNamespace(is_web=True)),
            patch("backend.main._hq_catalog_items") as hq_items,
            patch("backend.main._light_novel_catalog_items") as novel_items,
        ):
            result = main._build_search_payload(
                "hunter x hunter", "", 8, 0, BackgroundTasks()
            )

        self.assertEqual(result["items"][0]["title"], "Hunter x Hunter")
        hq_items.assert_not_called()
        novel_items.assert_not_called()

    def test_external_result_is_persisted_then_postgres_serves_it(self) -> None:
        repository = FakeCatalogRepository()
        main.catalog_repository = repository
        remote = manga("Vinland Saga", "mangalivre")
        with (
            patch(
                "backend.main._foreground_external_search",
                return_value=([remote], [], [{"source": "mangalivre"}]),
            ) as external,
            patch("backend.main._schedule_search_refresh", return_value=True),
        ):
            first = main._search_mangas("vinland saga", 8)
            main.search_cache.clear()  # simula restart do cache descartavel
            second = main._search_mangas("VINLAND SAGA", 8)

        self.assertEqual(first["items"][0]["title"], "Vinland Saga")
        self.assertEqual(second["items"][0]["title"], "Vinland Saga")
        self.assertEqual(repository.upsert_calls, 1)
        external.assert_called_once()

    def test_stuck_provider_cannot_exceed_foreground_budget(self) -> None:
        main.catalog_repository = FakeCatalogRepository()

        def stuck_provider(*_args) -> list[dict]:
            time.sleep(0.2)
            return []

        with (
            patch("backend.main.FOREGROUND_SEARCH_BUDGET_SECONDS", 0.03),
            patch("backend.main._search_sources", return_value=["mangalivre"]),
            patch("backend.main._search_source", side_effect=stuck_provider),
            patch("backend.main._schedule_search_refresh", return_value=True),
        ):
            started = time.perf_counter()
            result = main._search_mangas("never indexed title", 8)
            duration = time.perf_counter() - started

        self.assertEqual(result["items"], [])
        self.assertLess(duration, 0.15)
        self.assertIn("timeout", result["errors"][0])

    def test_home_reads_persistent_catalog_before_filesystem_snapshot(self) -> None:
        main.catalog_repository = FakeCatalogRepository([manga()])
        with (
            patch("backend.main._read_catalog_snapshot") as snapshot,
            patch("backend.main._schedule_catalog_refresh"),
        ):
            result = main._build_catalog(8)

        self.assertTrue(result["persistent"])
        self.assertEqual(result["items"][0]["title"], "Hunter x Hunter")
        snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
