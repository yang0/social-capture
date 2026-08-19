from __future__ import annotations

import io
import unittest

from PIL import Image

from social_capture.splitting import max_height_for_ratio, plan_slices, split_image


def png(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class SplittingTests(unittest.TestCase):
    def test_ratio_and_boundary(self):
        width, height = 900, 3000
        maximum = max_height_for_ratio(width)
        pieces = plan_slices(width, height, [{"y": 1200}, {"y": 2100}], overlap=64)
        self.assertTrue(all(piece.height <= maximum for piece in pieces))
        self.assertEqual(pieces[0].mode, "content-boundary")
        self.assertEqual(pieces[-1].bottom, height)

    def test_hard_cut_has_overlap_and_no_empty_tail(self):
        pieces = plan_slices(900, 4000, [], overlap=64)
        for previous, current in zip(pieces, pieces[1:]):
            self.assertEqual(previous.bottom - current.top, 64)
            self.assertGreater(current.height, 0)

    def test_split_writes_hash_and_actual_last_height(self):
        pieces = split_image(png(90, 400), output_dir=None, prefix="x", overlap=4)
        self.assertGreater(pieces[-1]["height"], 0)
        self.assertLessEqual(pieces[-1]["height"], max_height_for_ratio(90))
        self.assertEqual(len(pieces[0]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
