from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from backend import main
from backend.catalog_index import CatalogMemoryIndex


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
        self.search_calls = 0
        self.home_calls = 0

    def list_index_items(self) -> list[dict]:
        return [dict(item) for item in self.items]

    def search(self, query: str, limit: int) -> list[dict]:
        self.search_calls += 1
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
        self.home_calls += 1
        return [dict(item) for item in self.items if item.get("catalog_home_ready")][:limit]


class PostgresFirstSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_repository = main.catalog_repository
        self.original_memory_index = main.catalog_memory_index
        self.original_catalog_cache = main.catalog_cache
        main.catalog_memory_index = CatalogMemoryIndex()
        main.search_cache.clear()
        main.catalog_cache = None

    def tearDown(self) -> None:
        main.catalog_repository = self.original_repository
        main.catalog_memory_index = self.original_memory_index
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

    def test_resident_index_hit_skips_postgres_search(self) -> None:
        repository = FakeCatalogRepository([manga()])
        main.catalog_repository = repository
        main.catalog_memory_index.rebuild(repository.list_index_items())
        repository.search_calls = 0

        result = main._search_mangas("hunter x hunter", 8)

        self.assertEqual(result["items"][0]["title"], "Hunter x Hunter")
        self.assertEqual(repository.search_calls, 0)

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

    def test_fresh_catalog_result_does_not_start_external_refresh(self) -> None:
        fresh = manga()
        fresh["_catalog_last_seen_at"] = time.time()
        main.catalog_repository = FakeCatalogRepository([fresh])
        with patch("backend.main._schedule_search_refresh") as schedule:
            result = main._search_mangas("hunter x hunter", 8)

        self.assertEqual(result["items"][0]["title"], "Hunter x Hunter")
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
            main.search_cache.clear()
            main.catalog_memory_index = CatalogMemoryIndex()  # simula restart da RAM
            main.catalog_memory_index.rebuild(repository.list_index_items())
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

    def test_home_uses_resident_snapshot_without_postgres_round_trip(self) -> None:
        repository = FakeCatalogRepository([manga()])
        main.catalog_repository = repository
        main.catalog_memory_index.rebuild(repository.list_index_items())
        repository.home_calls = 0

        with patch("backend.main._schedule_catalog_refresh"):
            result = main._build_catalog(8)

        self.assertTrue(result["persistent"])
        self.assertEqual(repository.home_calls, 0)

    def test_failed_database_write_does_not_update_resident_index(self) -> None:
        repository = FakeCatalogRepository([manga()])
        main.catalog_repository = repository
        main.catalog_memory_index.rebuild(repository.list_index_items())
        before = main.catalog_memory_index.stats()["item_count"]

        with patch.object(repository, "upsert_many", side_effect=RuntimeError("database down")):
            persisted = main._persist_catalog_items([manga("Berserk")])

        self.assertEqual(persisted, 0)
        self.assertEqual(main.catalog_memory_index.stats()["item_count"], before)
        self.assertEqual(main.catalog_memory_index.search("berserk", 5), [])

    def test_startup_rebuilds_resident_index_from_postgres(self) -> None:
        main.catalog_repository = FakeCatalogRepository([manga()])

        metrics = main._initialize_catalog_memory_index()

        self.assertTrue(metrics["catalog_index_ready"])
        self.assertEqual(metrics["catalog_index_items"], 1)
        self.assertEqual(main.startup_metrics["catalog_index_items"], 1)
        self.assertEqual(main.catalog_memory_index.search("hunter x hunter", 5)[0]["title"], "Hunter x Hunter")

    def test_search_response_exposes_resident_index_timings(self) -> None:
        main.catalog_repository = FakeCatalogRepository([manga()])
        with (
            patch("backend.main._schedule_search_refresh", return_value=False),
            TestClient(main.app) as client,
        ):
            response = client.get("/api/search", params={"q": "hunter x hunter", "limit": 8})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-kari-index-hit"], "true")
        self.assertEqual(response.headers["x-kari-cache-hit"], "false")
        self.assertIn("memory_index;dur=", response.headers["server-timing"])
        self.assertIn("postgres;dur=0.00", response.headers["server-timing"])

    def test_browser_catalog_endpoint_exposes_only_public_fields(self) -> None:
        item = manga()
        item["cover_url"] = "https://example.test/cover.jpg?X-Amz-Signature=private"
        main.catalog_repository = FakeCatalogRepository([item])
        with TestClient(main.app) as client:
            response = client.get("/api/catalog-index")

        self.assertEqual(response.status_code, 200)
        public_item = response.json()["items"][0]
        self.assertEqual(public_item["cover_url"], "")
        self.assertNotIn("_catalog_last_seen_at", public_item)
        self.assertNotIn("payload", public_item)


if __name__ == "__main__":
    unittest.main()
