from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.catalog_index import CatalogMemoryIndex


def catalog_item(
    title: str = "Hunter x Hunter",
    *,
    aliases: list[str] | None = None,
    provider: str = "mangalivre",
    home_ready: bool = True,
) -> dict:
    slug = title.casefold().replace(" ", "-")
    return {
        "canonical_title": title,
        "alternative_titles": aliases or [],
        "provider": provider,
        "source": provider,
        "source_url": f"https://example.test/{provider}/{slug}",
        "cover_url": "https://example.test/cover.jpg",
        "genres": ["Aventura"],
        "chapter_count": 420,
        "chapter_count_verified": True,
        "latest_chapter": "420",
        "chapter_preview": ["420", "419", "418"],
        "chapter_languages": ["pt-br"],
        "_catalog_last_seen_at": 1234.0,
        "catalog_home_ready": home_ready,
    }


class CatalogMemoryIndexTests(unittest.TestCase):
    def test_exact_alias_prefix_and_unicode_search(self) -> None:
        index = CatalogMemoryIndex()
        index.rebuild([
            catalog_item("Hunter x Hunter", aliases=["HUNTER × HUNTER"]),
            catalog_item("JoJo's Bizarre Adventure", aliases=["JoJo no Kimyou na Bouken"]),
        ])

        self.assertEqual(index.search("hunter × hunter", 5)[0]["title"], "Hunter x Hunter")
        self.assertEqual(index.search("jojo no kimyou", 5)[0]["title"], "JoJo's Bizarre Adventure")
        self.assertEqual(index.search("Hunter x", 5)[0]["title"], "Hunter x Hunter")

    def test_fuzzy_search_is_small_and_bounded(self) -> None:
        index = CatalogMemoryIndex()
        stats = index.rebuild([catalog_item("Vinland Saga")])

        self.assertEqual(index.search("vinland saag", 5)[0]["title"], "Vinland Saga")
        self.assertEqual(stats["item_count"], 1)
        self.assertGreater(stats["memory_bytes"], 0)
        self.assertLess(stats["memory_bytes"], 32_000)

    def test_rebuild_is_atomic_when_projection_fails(self) -> None:
        index = CatalogMemoryIndex()
        index.rebuild([catalog_item()])
        invalid = catalog_item("Broken")
        invalid["genres"] = object()

        with self.assertRaises(TypeError):
            index.rebuild([catalog_item(), invalid])

        self.assertEqual(index.search("hunter", 5)[0]["title"], "Hunter x Hunter")

    def test_upsert_replaces_same_source_after_database_commit(self) -> None:
        index = CatalogMemoryIndex()
        index.rebuild([catalog_item()])
        updated = catalog_item()
        updated["chapter_count"] = 421

        self.assertEqual(index.upsert_many([updated]), 1)
        self.assertEqual(index.search("hunter x hunter", 5)[0]["chapter_count"], 421)
        self.assertEqual(index.stats()["item_count"], 1)

    def test_home_snapshot_contains_only_ready_items(self) -> None:
        index = CatalogMemoryIndex()
        index.rebuild([
            catalog_item("Hunter x Hunter"),
            catalog_item("Hidden Draft", home_ready=False),
        ])

        self.assertEqual([item["title"] for item in index.list_home(10)], ["Hunter x Hunter"])

    def test_ten_concurrent_searches_see_a_consistent_snapshot(self) -> None:
        index = CatalogMemoryIndex()
        index.rebuild([catalog_item("One Piece")])

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(
                lambda _: index.search("one piece", 5)[0]["title"],
                range(10),
            ))

        self.assertEqual(results, ["One Piece"] * 10)


if __name__ == "__main__":
    unittest.main()
