from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from social_capture.errors import OutputExistsError
from social_capture.models import CaptureArtifact, CapturedImage, CaptureOptions, CaptureReference
from social_capture.output import OutputWriter


class OutputTests(unittest.TestCase):
    def _artifact(self) -> tuple[CaptureReference, CaptureArtifact, CaptureOptions]:
        from tests.test_splitting import png

        reference = CaptureReference("x", "123", "https://x.com/u/status/123", "https://x.com/u/status/123")
        artifact = CaptureArtifact((CapturedImage(png(90, 90)),), "tweet-card")
        return reference, artifact, CaptureOptions(Path("unused"))

    def test_preflight_requires_explicit_target_and_rejects_existing(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "out"
            writer = OutputWriter(target)
            writer.preflight()
            (target / "manifest.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(OutputExistsError):
                OutputWriter(target).preflight()

    def test_overwrite_removes_only_owned_images(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "out"
            (target / "images").mkdir(parents=True)
            (target / "images" / "x-old-01-of-01.png").write_bytes(b"old")
            unrelated = target / "images" / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            (target / "manifest.json").write_text(
                json.dumps({"tool": "social-capture", "items": [{"parts": [{"path": "images/x-old-01-of-01.png"}]}]}),
                encoding="utf-8",
            )
            writer = OutputWriter(target, overwrite=True)
            writer.preflight()
            self.assertFalse((target / "images" / "x-old-01-of-01.png").exists())
            self.assertTrue(unrelated.exists())

    def test_manifest_cross_volume_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "out"
            writer = OutputWriter(target)
            writer.preflight()
            with patch("pathlib.Path.replace", side_effect=OSError("cross-device")):
                path = writer.write_manifest([])
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "complete")


if __name__ == "__main__":
    unittest.main()
