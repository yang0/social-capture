from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from social_capture.auth import load_auth_material, redact_source_url, redact_text
from social_capture.registry import search_backend_status


class AuthAndSearchTests(unittest.TestCase):
    def test_cookie_precedence_and_redaction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cookies.txt"
            path.write_text("sid=file-secret; foo=bar", encoding="utf-8")
            material = load_auth_material("x", cookie_file=path, environ={"X_COOKIE": "sid=env-secret"})
        self.assertEqual(material.source, "cookie-file")
        self.assertNotIn("secret", redact_text("xsec_token=secret"))
        self.assertNotIn("secret", redact_source_url("https://xhslink.com/a?id=1&xsec_token=secret"))

    def test_search_backends_are_machine_readable_and_not_built_in(self):
        rows = search_backend_status()
        self.assertEqual({row["platform"] for row in rows}, {"weibo", "zhihu", "x", "xiaohongshu", "douyin"})
        for row in rows:
            self.assertFalse(row["built_in"])
            self.assertTrue(row["repo_url"].startswith("https://github.com/"))
            self.assertIn("install_hint", row)
            self.assertIn("command_hint", row)


if __name__ == "__main__":
    unittest.main()
