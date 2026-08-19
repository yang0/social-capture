from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from social_capture.cli import main


class CliTests(unittest.TestCase):
    def test_providers_and_search_backends_json_are_read_only(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["providers", "--json"]), 0)
            self.assertEqual(main(["search-backends", "--json"]), 0)
            doctor_code = main(["doctor", "--json"])
            self.assertIn(doctor_code, (0, 2))
        payloads = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(payloads), 3)
        self.assertFalse(any(Path.cwd().glob("manifest.json")))

    def test_capture_requires_output_dir(self):
        with self.assertRaises(SystemExit):
            main(["capture", "https://x.com/u/status/123"])

    def test_capture_bad_input_writes_explicit_manifest_only(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "out"
            code = main(["capture", "https://example.com/not-social", "--output-dir", str(target), "--json"])
            self.assertEqual(code, 2)
            self.assertTrue((target / "manifest.json").exists())
            self.assertFalse((Path.cwd() / "manifest.json").exists())

    def test_platform_specific_options_do_not_silently_cross_platforms(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "out"
            code = main(
                [
                    "capture",
                    "https://weibo.com/u/123",
                    "--x-cut-stats",
                    "--output-dir",
                    str(target),
                ]
            )
            self.assertEqual(code, 2)
            self.assertFalse((target / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
