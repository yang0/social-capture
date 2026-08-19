"""Static built-in Provider registry and URL autodetection."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .models import ProviderHealth
from .providers.base import CaptureProvider
from .providers.douyin import DouyinProvider
from .providers.weibo import WeiboProvider
from .providers.x import XProvider
from .providers.xiaohongshu import XiaohongshuProvider
from .providers.zhihu import ZhihuProvider
from .search import SEARCH_BACKENDS, SearchBackendHint

BUILTIN_PROVIDERS: tuple[CaptureProvider, ...] = (
    WeiboProvider(),
    ZhihuProvider(),
    XProvider(),
    XiaohongshuProvider(),
    DouyinProvider(),
)
PROVIDERS_BY_NAME = {provider.name: provider for provider in BUILTIN_PROVIDERS}


def list_providers() -> tuple[CaptureProvider, ...]:
    return BUILTIN_PROVIDERS


def get_provider(name: str) -> CaptureProvider:
    try:
        return PROVIDERS_BY_NAME[str(name).strip().lower()]
    except KeyError as exc:
        choices = ", ".join(PROVIDERS_BY_NAME)
        raise ValueError(f"未知平台 {name!r}；可用平台: {choices}") from exc


def detect_provider(value: str) -> CaptureProvider:
    matches = [provider for provider in BUILTIN_PROVIDERS if provider.can_handle(value)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Numeric IDs are intentionally treated as Weibo IDs; otherwise a
        # future provider cannot make autodetection silently ambiguous.
        for provider in matches:
            if provider.name == "weibo":
                return provider
    raise ValueError("无法根据输入识别平台；请使用 --platform 明确指定")


def provider_health() -> list[ProviderHealth]:
    return [provider.check() for provider in BUILTIN_PROVIDERS]


def _hint_installed(hint: SearchBackendHint) -> bool:
    markers = hint.package_markers
    for marker in markers:
        if shutil.which(marker):
            return True
    # Explicit locations make doctor useful without scanning the workspace or
    # cloning anything. This is intentionally opt-in and read-only.
    for env_name in ("SOCIAL_CAPTURE_SEARCH_BACKEND", "MEDIACRAWLER_HOME"):
        value = os.environ.get(env_name)
        if value and Path(value).exists() and any(marker.lower() in value.lower() for marker in markers):
            return True
    return False


def search_backend_status(platform: str | None = None) -> list[dict[str, object]]:
    hints = [SEARCH_BACKENDS[platform]] if platform and platform in SEARCH_BACKENDS else SEARCH_BACKENDS.values()
    return [hint.as_dict(installed=_hint_installed(hint)) for hint in hints]
