from __future__ import annotations

import unittest

from backend import main


class OAuthCallbackHtmlSecurityTests(unittest.TestCase):
    def _assert_safe_popup(self, response) -> None:
        body = response.body.decode("utf-8")
        self.assertNotIn("</script><script>alert(1)</script>", body)
        self.assertIn("\\u003c/script\\u003e", body)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        policy = response.headers["content-security-policy"]
        self.assertIn("default-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("script-src 'nonce-", policy)

    def test_account_link_popup_escapes_script_termination(self) -> None:
        response = main._oauth_html(
            {"ok": False, "detail": "</script><script>alert(1)</script>"}
        )
        self._assert_safe_popup(response)

    def test_login_popup_escapes_script_termination_while_carrying_token(self) -> None:
        response = main._auth_html(
            {
                "ok": True,
                "token": "opaque-token",
                "detail": "</script><script>alert(1)</script>",
            }
        )
        self._assert_safe_popup(response)
        self.assertIn("opaque-token", response.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
