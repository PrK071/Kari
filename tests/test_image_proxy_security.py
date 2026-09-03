from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from backend import main
from backend.config import load_settings
from backend.network_security import UnsafeRemoteURLError, validate_public_http_url


def _address(host: str, port: int, *_args, **_kwargs):
    address = "93.184.216.34" if host == "images.example.test" else host
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]


class _Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status_code = status
        self.headers = headers
        self.body = body
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self.body

    def close(self) -> None:
        self.closed = True


class PublicURLValidationTests(unittest.TestCase):
    def test_rejects_loopback_link_local_and_credentials(self) -> None:
        for url in (
            "http://127.0.0.1/private",
            "http://169.254.169.254/latest/meta-data",
            "http://user:password@8.8.8.8/image.png",
        ):
            with self.assertRaises(UnsafeRemoteURLError):
                validate_public_http_url(url)

    @patch("backend.network_security.socket.getaddrinfo")
    def test_rejects_hostname_resolving_to_private_ip(self, resolver) -> None:
        resolver.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 443))
        ]

        with self.assertRaises(UnsafeRemoteURLError):
            validate_public_http_url("https://images.example.test/page.png")

    @patch("backend.network_security.socket.getaddrinfo", side_effect=_address)
    def test_accepts_public_http_destination(self, _resolver) -> None:
        self.assertEqual(
            validate_public_http_url("https://images.example.test/page.png"),
            "https://images.example.test/page.png",
        )


class ImageProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_settings = main.settings
        main.settings = load_settings({"KARI_RUNTIME": "web"})
        main.image_cache.clear()
        main.image_inflight.clear()

    def tearDown(self) -> None:
        main.settings = self.original_settings
        main.image_cache.clear()
        main.image_inflight.clear()

    @patch("backend.network_security.socket.getaddrinfo", side_effect=_address)
    def test_does_not_follow_redirect_to_loopback(self, _resolver) -> None:
        redirect = _Response(302, {"location": "http://127.0.0.1/private"})
        with patch.object(main._image_http, "get", return_value=redirect) as request:
            with self.assertRaises(UnsafeRemoteURLError):
                main._fetch_image("https://images.example.test/page.png")

        self.assertEqual(request.call_count, 1)
        self.assertTrue(redirect.closed)

    @patch("backend.network_security.socket.getaddrinfo", side_effect=_address)
    def test_accepts_small_raster_and_rejects_spoofed_content(self, _resolver) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"content"
        valid = _Response(200, {"content-type": "image/png"}, png)
        with patch.object(main._image_http, "get", return_value=valid):
            image = main._fetch_image("https://images.example.test/valid.png")
        self.assertEqual(image.media_type, "image/png")

        spoofed = _Response(200, {"content-type": "image/png"}, b"<html>not an image</html>")
        with patch.object(main._image_http, "get", return_value=spoofed):
            with self.assertRaises(RuntimeError):
                main._fetch_image("https://images.example.test/spoofed.png")


if __name__ == "__main__":
    unittest.main()
