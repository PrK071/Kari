from __future__ import annotations

import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import requests

from backend import main
from plugins.fliptru import FliptruPlugin


class _SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - assinatura do stdlib
        time.sleep(0.3)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"late")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *args) -> None:
        del args


class ProviderRequestTimeoutTests(unittest.TestCase):
    def test_provider_read_timeout_releases_the_http_call(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        plugin = FliptruPlugin(request_timeout=(0.05, 0.05))
        started = time.perf_counter()
        try:
            with self.assertRaises(requests.exceptions.ReadTimeout):
                plugin._get(f"http://127.0.0.1:{server.server_port}/slow")
            self.assertLess(time.perf_counter() - started, 0.2)
        finally:
            server.shutdown()
            server.server_close()
            plugin.session.close()

    def test_bounded_brasuka_search_does_not_multiply_timeout_with_retries(self) -> None:
        bounded_reader = main._search_reader("mangasbrasuka", (0.05, 0.05))
        with patch(
            "reader_server.requests.get",
            side_effect=requests.exceptions.ReadTimeout(),
        ) as request:
            with self.assertRaises(requests.exceptions.ReadTimeout):
                bounded_reader._mangasbrasuka_get_html(
                    "https://mangasbrasuka.com.br/manga/teste/"
                )

        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
