from __future__ import annotations

import re
from urllib.parse import urlparse

from ...errors import InputError
from ...models import CaptureArtifact, CapturedImage, CaptureOptions, CaptureReference
from ..base import CaptureProvider, clean_id
from ..common import (
    capture_locator,
    check_page_state,
    exact_locator_by_marker,
    hide_noise,
    open_site_page,
    page_text,
    wait_for_images,
)

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def parse_reference(value: str) -> CaptureReference:
    raw = str(value or "").strip()
    if not raw:
        raise InputError("微博 URL/ID 不能为空")
    if raw.isdigit():
        return CaptureReference("weibo", raw, f"https://m.weibo.cn/detail/{raw}", raw)
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"weibo.com", "www.weibo.com", "m.weibo.cn", "weibo.cn"}:
        raise InputError("微博只接受 weibo.com/m.weibo.cn 详情 URL 或数字 ID")
    parts = [part for part in parsed.path.split("/") if part]
    content_id = ""
    for marker in ("detail", "status"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                content_id = parts[index + 1]
                break
    if not content_id and parts:
        content_id = parts[-1]
    if not content_id or not _ID_RE.fullmatch(content_id):
        raise InputError("无法从微博链接识别微博 ID")
    return CaptureReference("weibo", clean_id(content_id), raw, raw)


class WeiboProvider(CaptureProvider):
    name = "weibo"
    hosts = ("weibo.com", "www.weibo.com", "m.weibo.cn", "weibo.cn")
    capabilities = ("post-card", "retweet-card", "multi-image", "video-cover", "long-content")

    def can_handle(self, value: str) -> bool:
        raw = str(value or "").strip()
        if raw.isdigit():
            return True
        host = (urlparse(raw).hostname or "").lower()
        return host in self.hosts

    def parse_reference(self, value: str) -> CaptureReference:
        return parse_reference(value)

    async def capture(self, browser: object, reference: CaptureReference, options: CaptureOptions) -> CaptureArtifact:
        page = await open_site_page(browser, reference.url, options.wait_seconds)
        text = await page_text(page)
        check_page_state(text, platform="微博")
        try:
            await page.evaluate(
                """() => { for (const el of document.querySelectorAll('button,a,[role="button"]')) {
                  const value = (el.innerText || el.textContent || '').trim();
                  if (value === '全文' || value === '展开全文') { try { el.click(); } catch (_) {} }
                }}"""
            )
            await page.wait_for_timeout(500)
        except Exception:
            pass
        await hide_noise(
            page,
            ("nav", "aside", ".gn_header", ".WB_global_nav", ".comment", ".WB_feed_handle", ".footer"),
        )
        # Exact attributes/links are mandatory. A generic first article/card
        # fallback could otherwise capture a recommendation or another feed
        # item with the same visual structure.
        locator, selector = await exact_locator_by_marker(
            page,
            (
                f'[mid="{reference.content_id}"]',
                f'[data-mid="{reference.content_id}"]',
                ".WB_detail",
                "article[role=article]",
                "article",
                ".WB_feed_type",
                ".card",
            ),
            reference.content_id,
        )
        await wait_for_images(page, f"{selector} img, {selector} video")
        image, info = await capture_locator(
            locator,
            boundary_selectors=(
                ".WB_text", ".txt", ".wbpro-feed-ogText", ".wbpro-feed-reText",
                "p", "img", "video", ".WB_media_wrap", "[class*=retweet]",
            ),
        )
        title = (await locator.inner_text(timeout=3000)).strip()[:300]
        return CaptureArtifact(
            (CapturedImage(image, tuple(info.get("boundaries") or ())),),
            "post-card",
            split_long=True,
            metadata={"selector": selector, "title_excerpt": title, "clip": info},
        )
