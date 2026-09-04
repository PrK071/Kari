from __future__ import annotations

import unittest

from fastapi import HTTPException

from backend import main
from backend.config import load_settings


class RuntimeCapabilitiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_settings = main.settings

    def tearDown(self) -> None:
        main.settings = self.original_settings

    def test_web_runtime_disables_local_and_sakura_sources(self) -> None:
        main.settings = load_settings(
            {
                "KARI_RUNTIME": "web",
                "KARI_BACKEND_URL": "http://127.0.0.1:8000",
                "KARI_FRONTEND_URL": "http://127.0.0.1:5173",
            }
        )

        capabilities = main.capabilities()
        self.assertFalse(capabilities["local_libraries"])
        self.assertFalse(capabilities["sakura"])
        self.assertFalse(capabilities["dragontea"])
        self.assertNotIn("sakura", main._search_sources())
        self.assertNotIn("mangageek", main._search_sources())
        self.assertIsNone(
            main._normalize_manga_item(
                {
                    "title": "Local",
                    "url": "hq-local://comic/example",
                    "provider": "hq_local",
                }
            )
        )
        for source in (
            "hq-local://comic/example",
            "light-novel://novel/example",
            "https://sakuramangas.org/obras/example",
            "https://dragontea.ink/series/example",
            "mangageek://manga/4288",
        ):
            with self.assertRaises(HTTPException) as raised:
                main._ensure_source_allowed(source)
            self.assertEqual(raised.exception.status_code, 404)
        with self.assertRaises(HTTPException) as raised:
            main.hq_library("")
        self.assertEqual(raised.exception.status_code, 404)

    def test_desktop_runtime_preserves_local_sources(self) -> None:
        main.settings = load_settings({"KARI_RUNTIME": "desktop"})

        main._ensure_source_allowed("hq-local://comic/example")
        main._ensure_source_allowed("mangageek://manga/4288")
        self.assertTrue(main.capabilities()["local_libraries"])
        self.assertTrue(main.capabilities()["sakura"])
        self.assertTrue(main.capabilities()["dragontea"])


if __name__ == "__main__":
    unittest.main()
