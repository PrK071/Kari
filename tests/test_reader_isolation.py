from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import MethodType
from unittest.mock import patch

import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import main
from backend.config import load_settings
from backend.rate_limit import MemoryRateLimitBackend, RateLimiter
from reader_server import ChapterState, MangaReader


class ReaderEndpointIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = (main.settings, main.rate_limiter)
        main.settings = load_settings({"KARI_RUNTIME": "web"})
        main.rate_limiter = RateLimiter(MemoryRateLimitBackend())
        self.client = TestClient(main.app)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.client.close()
        main.settings, main.rate_limiter = self.original
        self.temp_dir.cleanup()

    def test_web_cannot_read_process_global_reader_image(self) -> None:
        latest_chapter_page = Path(self.temp_dir.name) / "latest.webp"
        latest_chapter_page.write_bytes(b"RIFFxxxxWEBP")

        with patch.object(
            main.reader,
            "get_image",
            return_value=(latest_chapter_page, "image/webp"),
        ) as get_image:
            response = self.client.get("/api/reader-image/1")

        self.assertEqual(response.status_code, 404)
        get_image.assert_not_called()

    def test_desktop_keeps_local_reader_image(self) -> None:
        main.settings = load_settings({"KARI_RUNTIME": "desktop"})
        page = Path(self.temp_dir.name) / "page.webp"
        page.write_bytes(b"RIFFxxxxWEBP")
        with patch.object(main.reader, "get_image", return_value=(page, "image/webp")):
            response = self.client.get("/api/reader-image/1")
        self.assertEqual(response.status_code, 200)

    def test_web_payload_rejects_pages_that_require_process_state(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            main._sanitize_web_chapter_payload(
                {"images": [{"index": 1, "source_url": "hqfile://private/page"}]}
            )
        self.assertEqual(raised.exception.status_code, 502)

        payload = {
            "cache": {"directory": "C:/private/cache"},
            "images": [{"index": 1, "source_url": "https://cdn.example/a.webp"}],
        }
        main._sanitize_web_chapter_payload(payload)
        self.assertNotIn("cache", payload)


class MangaReaderStateTests(unittest.TestCase):
    def test_concurrent_stateless_chapters_keep_payloads_isolated(self) -> None:
        reader = MangaReader(main.reader.args)

        def load_chapter(instance, url: str, include_neighbors: bool = True):
            del include_neighbors
            label = url.rsplit("/", 1)[-1]
            instance.state = ChapterState(
                url=url,
                label=label,
                image_urls=[f"https://cdn.example/{label}/page-1.webp"],
                cache_dir=instance.cache.new_chapter_dir(label),
                session=requests.Session(),
            )
            return {"provider": "test", "url": url}

        reader.load_chapter = MethodType(load_chapter, reader)
        urls = ("https://source.example/chapter-a", "https://source.example/chapter-b")
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                payloads = list(
                    executor.map(
                        lambda url: reader.chapter_metadata(
                            url,
                            include_source_urls=True,
                            retain_state=False,
                        ),
                        urls,
                    )
                )
            self.assertIsNone(reader.state)
            self.assertEqual(
                {payload["chapter"]["url"] for payload in payloads},
                set(urls),
            )
            for payload in payloads:
                label = payload["chapter"]["url"].rsplit("/", 1)[-1]
                self.assertEqual(
                    payload["images"][0]["source_url"],
                    f"https://cdn.example/{label}/page-1.webp",
                )
        finally:
            reader.close()


if __name__ == "__main__":
    unittest.main()
