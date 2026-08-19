"""Provider protocol and small base implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import CaptureArtifact, CaptureOptions, CaptureReference, ProviderHealth


class CaptureProvider(ABC):
    name: str = ""
    hosts: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    @abstractmethod
    def can_handle(self, value: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse_reference(self, value: str) -> CaptureReference:
        raise NotImplementedError

    def check(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "Provider 已加载", self.capabilities)

    @abstractmethod
    async def capture(
        self,
        browser: Any,
        reference: CaptureReference,
        options: CaptureOptions,
    ) -> CaptureArtifact:
        raise NotImplementedError


def clean_id(value: str, fallback: str = "content") -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return cleaned or fallback
