"""Public data contracts shared by the CLI and platform providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CaptureReference:
    """A normalized, platform-specific capture target."""

    platform: str
    content_id: str
    url: str
    input_value: str
    content_type: str = "post-card"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureOptions:
    """Options that are safe to pass to every provider."""

    output_dir: Path
    cdp_url: str = "http://127.0.0.1:9221"
    cookie_header: str | None = None
    wait_seconds: float = 3.0
    overlap: int = 64
    max_ratio_width: int = 9
    max_ratio_height: int = 16
    overwrite: bool = False
    x_cut_stats: bool = False
    xhs_comments: int = 0
    xhs_all_images: bool = False
    douyin_rows: int = 2


@dataclass(frozen=True)
class CapturedImage:
    """One browser screenshot before optional long-image splitting."""

    data: bytes
    boundaries: tuple[Mapping[str, Any] | int, ...] = ()
    label: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureArtifact:
    """Provider output. The output layer owns filenames and manifests."""

    images: tuple[CapturedImage, ...]
    capture_mode: str
    split_long: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    available: bool
    message: str
    capabilities: tuple[str, ...] = ()


@dataclass
class CaptureResult:
    """One manifest item in an in-memory form."""

    input_value: str
    platform: str
    content_id: str
    source_url: str
    content_type: str
    status: str = "error"
    capture_mode: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    parts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "input": self.input_value,
            "platform": self.platform,
            "content_id": self.content_id,
            "source_url": self.source_url,
            "content_type": self.content_type,
            "status": self.status,
            "capture_mode": self.capture_mode,
            **self.metadata,
            "parts": self.parts,
            "error": self.error,
            "error_kind": self.error_kind,
        }
