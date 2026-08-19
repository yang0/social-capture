from __future__ import annotations

import hashlib
import io
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from ..errors import ImageError


@dataclass(frozen=True)
class CropSlice:
    index: int
    total: int
    top: int
    bottom: int
    overlap: int
    mode: str

    @property
    def height(self) -> int:
        return self.bottom - self.top


def max_height_for_ratio(width: int, ratio_width: int = 9, ratio_height: int = 16) -> int:
    if width <= 0 or ratio_width <= 0 or ratio_height <= 0:
        raise ValueError("宽度和比例必须为正数")
    return max(1, math.floor(width * ratio_height / ratio_width))


def _boundary_value(boundary: Any) -> int | None:
    if isinstance(boundary, (int, float)):
        return int(boundary)
    if isinstance(boundary, dict):
        for key in ("y", "bottom", "end", "top"):
            try:
                if key in boundary:
                    return int(float(boundary[key]))
            except (TypeError, ValueError):
                return None
    return None


def plan_slices(
    width: int,
    height: int,
    boundaries: Iterable[Any] = (),
    *,
    overlap: int = 64,
    ratio_width: int = 9,
    ratio_height: int = 16,
) -> list[CropSlice]:
    """Plan monotonic crops and prefer DOM content boundaries when available.

    A hard cut uses ``overlap`` pixels. A semantic boundary deliberately has
    no overlap because it already lands between content blocks. Every returned
    crop obeys the requested width:height ratio.
    """

    if width <= 0 or height <= 0:
        raise ValueError("图片尺寸必须为正数")
    if overlap < 0:
        raise ValueError("重叠像素不能为负数")
    max_height = max_height_for_ratio(width, ratio_width, ratio_height)
    points = sorted(
        {
            max(1, min(height - 1, value))
            for value in (_boundary_value(item) for item in boundaries)
            if value is not None
        }
    )
    if height <= max_height:
        return [CropSlice(1, 1, 0, height, 0, "single")]

    planned: list[tuple[int, int, int, str]] = []
    current = 0
    while current < height:
        limit = min(height, current + max_height)
        if limit == height:
            end = height
            used_overlap = 0
            mode = "final"
            next_current = height
        else:
            # Do not choose a boundary too close to the top; that creates a
            # nearly empty piece and is the source of the historic whitespace
            # problem in multi-part captures.
            candidates = [
                point
                for point in points
                if current + max_height // 2 <= point <= limit and point > current
            ]
            if candidates:
                end = max(candidates)
                used_overlap = 0
                mode = "content-boundary"
                next_current = end
            else:
                end = limit
                used_overlap = min(overlap, max(0, end - current - 1))
                mode = "hard-cut"
                next_current = max(current + 1, end - used_overlap)
        if end <= current:
            raise ValueError("无法生成单调递增的截图分片")
        planned.append((current, end, used_overlap, mode))
        current = next_current
    total = len(planned)
    return [
        CropSlice(index, total, top, bottom, used_overlap, mode)
        for index, (top, bottom, used_overlap, mode) in enumerate(planned, 1)
    ]


def _load_image(image_bytes: bytes) -> Image.Image:
    if not image_bytes:
        raise ImageError("截图数据为空")
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        return image.convert("RGBA")
    except Exception as exc:  # Pillow raises several format-specific errors.
        raise ImageError("截图不是有效图片") from exc


def split_image(
    image_bytes: bytes,
    boundaries: Iterable[Any] = (),
    *,
    output_dir: str | Path | None = None,
    prefix: str = "capture",
    overlap: int = 64,
    ratio_width: int = 9,
    ratio_height: int = 16,
) -> list[dict[str, Any]]:
    """Split image bytes and optionally write numbered PNGs."""

    image = _load_image(image_bytes)
    slices = plan_slices(
        image.width,
        image.height,
        boundaries,
        overlap=overlap,
        ratio_width=ratio_width,
        ratio_height=ratio_height,
    )
    output = Path(output_dir) if output_dir else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    for piece in slices:
        cropped = image.crop((0, piece.top, image.width, piece.bottom))
        stream = io.BytesIO()
        cropped.save(stream, format="PNG")
        data = stream.getvalue()
        filename = f"{prefix}-{piece.index:02d}-of-{piece.total:02d}.png"
        path = output / filename if output else None
        if path:
            path.write_bytes(data)
        result.append(
            {
                "filename": filename,
                "path": str(path) if path else None,
                "width": cropped.width,
                "height": cropped.height,
                "coordinates": {
                    "x": 0,
                    "y": piece.top,
                    "width": cropped.width,
                    "height": cropped.height,
                },
                "overlap": piece.overlap,
                "mode": piece.mode,
                "sha256": hashlib.sha256(data).hexdigest(),
                "data": data if output is None else None,
            }
        )
    return result
