from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from ..auth import redact_source_url
from ..errors import ImageError, OutputExistsError
from ..models import CaptureArtifact, CaptureOptions, CaptureReference, CaptureResult
from ..splitting import split_image

_INVALID_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_OWNED_IMAGE = re.compile(r"^(?:weibo|zhihu|x|xiaohongshu|douyin)-[A-Za-z0-9_-]+-\d{2,}-of-\d{2,}\.png$")


def safe_filename(value: str, fallback: str = "capture", limit: int = 80) -> str:
    text = _INVALID_FILENAME.sub("_", str(value or "")).strip(" .")
    return (text or fallback)[: max(1, limit)]


def _normalise_png(data: bytes) -> tuple[bytes, int, int]:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue(), image.width, image.height
    except Exception as exc:
        raise ImageError("浏览器返回的数据不是有效 PNG/图片") from exc


def _metadata_without_secrets(value: Any) -> Any:
    """Drop keys that providers must never persist in a manifest."""

    secret_names = {"cookie", "cookies", "xsec_token", "token", "signature", "sign", "auth"}
    if isinstance(value, dict):
        return {
            str(key): _metadata_without_secrets(item)
            for key, item in value.items()
            if str(key).lower() not in secret_names
        }
    if isinstance(value, list):
        return [_metadata_without_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [_metadata_without_secrets(item) for item in value]
    return value


class OutputWriter:
    """Write one explicit output directory and its public manifest."""

    def __init__(self, output_dir: str | Path, *, overwrite: bool = False):
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.image_dir = self.output_dir / "images"
        self.overwrite = overwrite
        self._started = False

    def preflight(self) -> None:
        if self.output_dir.exists() and not self.output_dir.is_dir():
            raise OutputExistsError(f"输出路径不是目录: {self.output_dir}")
        manifest = self.output_dir / "manifest.json"
        images = self.output_dir / "images"
        if not self.overwrite and (manifest.exists() or (images.exists() and any(images.iterdir()))):
            raise OutputExistsError(
                f"输出目录已有结果: {self.output_dir}；如需覆盖请显式使用 --overwrite"
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        if self.overwrite and self.image_dir.exists():
            self._prepare_overwrite(manifest)
        self._started = True

    def _prepare_overwrite(self, manifest: Path) -> None:
        """Remove only image paths proven by the previous social-capture manifest."""

        if not manifest.exists():
            owned = [path for path in self.image_dir.iterdir() if path.is_file() and _OWNED_IMAGE.fullmatch(path.name)]
            if owned:
                raise OutputExistsError(
                    "输出目录有疑似 social-capture 图片但没有可核验 manifest.json；请清理后重试"
                )
            return
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OutputExistsError("已有 manifest.json 无法核验；请手动处理后再使用 --overwrite") from exc
        if payload.get("tool") != "social-capture":
            raise OutputExistsError("已有 manifest.json 不属于 social-capture；拒绝覆盖")
        image_root = self.image_dir.resolve()
        paths: set[Path] = set()
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            for part in item.get("parts", []):
                if not isinstance(part, dict) or not part.get("path"):
                    continue
                relative = Path(str(part["path"]))
                candidate = (self.output_dir / relative).resolve()
                if candidate.parent == image_root and _OWNED_IMAGE.fullmatch(candidate.name):
                    paths.add(candidate)
        for path in paths:
            if path.is_file():
                path.unlink()
        # Do not guess at unlisted files. A stale file with our naming pattern
        # is a safe hard failure; unrelated user files are preserved.
        stale = [path for path in self.image_dir.iterdir() if path.is_file() and _OWNED_IMAGE.fullmatch(path.name)]
        if stale:
            raise OutputExistsError(
                "输出目录有未被旧 manifest 列出的 social-capture 图片；拒绝删除，请先处理后重试"
            )

    def _write_piece(self, data: bytes, filename: str) -> Path:
        if not self._started:
            self.preflight()
        path = self.image_dir / filename
        if path.exists() and not self.overwrite:
            raise OutputExistsError(f"输出文件已存在: {path}")
        path.write_bytes(data)
        return path

    def write_artifact(
        self,
        reference: CaptureReference,
        artifact: CaptureArtifact,
        options: CaptureOptions,
    ) -> CaptureResult:
        """Persist images and return a manifest-ready result item."""

        if not self._started:
            self.preflight()
        result = CaptureResult(
            input_value=reference.input_value,
            platform=reference.platform,
            content_id=reference.content_id,
            source_url=redact_source_url(reference.url),
            content_type=reference.content_type,
            status="ok",
            capture_mode=artifact.capture_mode,
            metadata=_metadata_without_secrets({**dict(reference.metadata), **dict(artifact.metadata)}),
        )
        pending: list[dict[str, Any]] = []
        prefix = f"{safe_filename(reference.platform)}-{safe_filename(reference.content_id)}"
        for image_index, captured in enumerate(artifact.images, 1):
            if artifact.split_long:
                pieces = split_image(
                    captured.data,
                    captured.boundaries,
                    output_dir=None,
                    prefix=prefix,
                    overlap=options.overlap,
                    ratio_width=options.max_ratio_width,
                    ratio_height=options.max_ratio_height,
                )
            else:
                data, width, height = _normalise_png(captured.data)
                pieces = [
                    {
                        "data": data,
                        "width": width,
                        "height": height,
                        "coordinates": {"x": 0, "y": 0, "width": width, "height": height},
                        "overlap": 0,
                        "mode": "card",
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                ]
            for piece in pieces:
                pending.append({**piece, "image_index": image_index, "label": captured.label})

        total = len(pending)
        for index, piece in enumerate(pending, 1):
            filename = f"{prefix}-{index:02d}-of-{total:02d}.png"
            data = piece.pop("data")
            path = self._write_piece(data, filename)
            part = {
                "path": str(path.relative_to(self.output_dir)).replace(os.sep, "/"),
                "filename": filename,
                "width": piece["width"],
                "height": piece["height"],
                "coordinates": piece["coordinates"],
                "overlap": piece["overlap"],
                "mode": piece["mode"],
                "sha256": piece["sha256"],
            }
            if piece.get("label"):
                part["label"] = piece["label"]
            result.parts.append(part)
        result.metadata["part_count"] = len(result.parts)
        return result

    def write_manifest(self, items: Iterable[CaptureResult], *, extra: dict[str, Any] | None = None) -> Path:
        if not self._started:
            self.preflight()
        rows = [item.as_dict() for item in items]
        success_count = sum(row.get("status") == "ok" for row in rows)
        failed_count = len(rows) - success_count
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "tool": "social-capture",
            "run_id": uuid.uuid4().hex,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "output_dir": str(self.output_dir),
            "count": len(rows),
            "success_count": success_count,
            "failed_count": failed_count,
            "status": "complete" if failed_count == 0 else ("partial" if success_count else "failed"),
            "items": rows,
        }
        if extra:
            manifest.update(_metadata_without_secrets(extra))
        destination = self.output_dir / "manifest.json"
        if destination.exists() and not self.overwrite:
            raise OutputExistsError(f"输出清单已存在: {destination}；如需覆盖请使用 --overwrite")
        # The temporary file is created in the system temp directory, never in
        # the repository or the user's output directory.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        try:
            try:
                temp_path.replace(destination)
            except OSError:
                # On Windows TEMP is commonly on C: while an explicit target
                # is on another volume. Path.replace cannot cross volumes;
                # copy the complete closed file instead, then remove the temp.
                shutil.copyfile(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)
        return destination


def build_manifest(output_dir: str | Path, items: Iterable[CaptureResult], *, overwrite: bool = False) -> Path:
    writer = OutputWriter(output_dir, overwrite=overwrite)
    writer.preflight()
    return writer.write_manifest(items)
