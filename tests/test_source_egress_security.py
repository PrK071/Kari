from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import main
from backend.config import load_settings


class SourceEgressSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_settings = main.settings
        main.settings = load_settings({"KARI_RUNTIME": "web"})

    def tearDown(self) -> None:
        main.settings = self.original_settings

    def test_web_rejects_internal_disguised_and_plain_http_sources(self) -> None:
        sources = (
            "http://127.0.0.1/mangadex.org/title/example",
            "https://evil.example/mangadex.org/title/example",
            "http://mangadex.org/title/00000000-0000-0000-0000-000000000000",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(HTTPException) as raised:
                main._ensure_source_allowed(source)
            self.assertEqual(raised.exception.status_code, 422)

    def test_web_rejects_browser_backed_sources(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            main._ensure_source_allowed("https://dragontea.ink/series/example")
        self.assertEqual(raised.exception.status_code, 404)

    @patch("backend.main.validate_public_http_url")
    def test_allowed_https_provider_still_gets_public_dns_validation(self, validate) -> None:
        source = "https://mangadex.org/title/00000000-0000-0000-0000-000000000000"
        validate.return_value = source

        main._ensure_source_allowed(source)

        validate.assert_called_once_with(source, allowed_ports={443})

    @patch.object(main.reader, "list_chapters")
    def test_chapter_fetch_checks_egress_before_reader(self, list_chapters) -> None:
        with self.assertRaises(HTTPException):
            main._resilient_list_chapters("http://169.254.169.254/latest/meta-data", "pt-br")
        list_chapters.assert_not_called()

    def test_valid_internal_provider_identifier_remains_supported(self) -> None:
        main._ensure_source_allowed(
            "mangadex://title/00000000-0000-0000-0000-000000000000"
        )

    @patch("backend.main._build_manga_meta")
    def test_manga_metadata_endpoint_validates_without_undefined_identity(self, build_meta) -> None:
        build_meta.return_value = {"title": "Safe"}
        source = "mangadex://title/00000000-0000-0000-0000-000000000000"
        main.manga_meta_cache.clear()
        with TestClient(main.app) as client:
            response = client.get("/api/manga-meta", params={"source_url": source})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["title"], "Safe")


if __name__ == "__main__":
    unittest.main()
