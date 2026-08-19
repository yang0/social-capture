from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from ...errors import InputError, TargetNotFoundError
from ...models import CaptureArtifact, CapturedImage, CaptureOptions, CaptureReference
from ..base import CaptureProvider
from ..common import capture_locator, check_page_state, open_site_page, page_text, wait_for_images

_STATUS_RE = re.compile(r"^/[^/]+/status/(\d+)(?:/|$)", re.IGNORECASE)


def parse_reference(value: str) -> CaptureReference:
    raw = str(value or "").strip()
    if raw.isdigit():
        return CaptureReference("x", raw, f"https://x.com/i/status/{raw}", raw, content_type="tweet")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        raise InputError("X 只接受 x.com/twitter.com 的推文 URL")
    match = _STATUS_RE.match(parsed.path)
    if not match:
        raise InputError("X URL 必须包含 /status/<数字推文ID>")
    canonical = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return CaptureReference("x", match.group(1), canonical, raw, content_type="tweet")


class XProvider(CaptureProvider):
    name = "x"
    hosts = ("x.com", "www.x.com", "twitter.com", "www.twitter.com")
    capabilities = ("post-card", "reply-card", "quote-card", "multi-image", "video-cover")

    def can_handle(self, value: str) -> bool:
        host = (urlparse(str(value or "")).hostname or "").lower()
        return host in self.hosts

    def parse_reference(self, value: str) -> CaptureReference:
        return parse_reference(value)

    async def capture(self, browser: object, reference: CaptureReference, options: CaptureOptions) -> CaptureArtifact:
        page = await open_site_page(browser, reference.url, options.wait_seconds)
        text = await page_text(page)
        check_page_state(text, platform="X")
        # Filter by the status link so a reply/recommendation above the target
        # can never silently become the captured card.
        locator = page.locator(
            'article[data-testid="tweet"]',
            has=page.locator(f'a[href*="/status/{reference.content_id}"]'),
        )
        if await locator.count() == 0:
            locator = page.locator(
                "article",
                has=page.locator(f'a[href*="/status/{reference.content_id}"]'),
            )
        if await locator.count() == 0:
            # X can mount the status article just after the first DOM pass.
            # Retry this narrow, recoverable state once; challenges and other
            # failures are not retried.
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(700)
            except Exception:
                pass
            check_page_state(await page_text(page), platform="X")
            locator = page.locator(
                'article[data-testid="tweet"]',
                has=page.locator(f'a[href*="/status/{reference.content_id}"]'),
            )
            if await locator.count() == 0:
                locator = page.locator("article", has=page.locator(f'a[href*="/status/{reference.content_id}"]'))
        if await locator.count() == 0:
            raise TargetNotFoundError("未找到与目标 status ID 精确匹配的 X 推文卡片")
        locator = locator.first
        await wait_for_images(page, "article[data-testid=\"tweet\"] img")
        if options.x_cut_stats:
            try:
                await locator.evaluate(
                    """el => el.querySelectorAll('[role="group"], [data-testid="reply"], [data-testid="retweet"], [data-testid="like"], [data-testid="bookmark"]').forEach(node => node.remove())"""
                )
            except Exception:
                pass
        image, info = await capture_locator(
            locator,
            boundary_selectors=("[data-testid=tweetText]", "img", "video", "blockquote", "[data-testid=card.wrapper]"),
        )
        handle = await locator.evaluate(
            r"""el => { for (const a of el.querySelectorAll('a[href]')) {
              const m = new URL(a.href, location.href).pathname.match(/^\/([^/]+)\/status\//);
              if (m) return m[1];
            } return null; }"""
        )
        return CaptureArtifact(
            (CapturedImage(image, tuple(info.get("boundaries") or ())),),
            "tweet-card",
            split_long=False,
            metadata={"selector": "article[data-testid=\"tweet\"]", "author_handle": handle, "cut_stats": bool(options.x_cut_stats), "clip": info},
        )
