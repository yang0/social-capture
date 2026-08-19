from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse

from ...auth import redact_source_url
from ...errors import InputError
from ...models import CaptureArtifact, CapturedImage, CaptureOptions, CaptureReference
from ..base import CaptureProvider, clean_id
from ..common import (
    capture_locator,
    check_page_state,
    first_visible_locator,
    hide_noise,
    open_site_page,
    page_text,
    wait_for_images,
)


def parse_reference(value: str) -> CaptureReference:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {
        "xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com"
    }:
        raise InputError("小红书只接受 xiaohongshu.com 或 xhslink.com 笔记 URL")
    if host in {"xiaohongshu.com", "www.xiaohongshu.com"} and not any(
        key.lower() == "xsec_token" for key in parse_qs(parsed.query, keep_blank_values=True)
    ):
        raise InputError("带签名的小红书详情 URL 必须包含 xsec_token；请提供完整分享链接")
    parts = [part for part in parsed.path.split("/") if part]
    content_id = ""
    for marker in ("explore", "discovery", "item"):
        if marker in parts and parts.index(marker) + 1 < len(parts):
            content_id = parts[parts.index(marker) + 1]
            break
    if not content_id and parts:
        content_id = parts[-1]
    if not content_id or not re.fullmatch(r"[A-Za-z0-9_-]+", content_id):
        raise InputError("无法从小红书链接识别笔记 ID")
    # xhslink query values often contain xsec_token. Keep it for navigation,
    # while output.py always redacts it in the manifest.
    canonical = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
    return CaptureReference("xiaohongshu", clean_id(content_id), canonical, raw, content_type="note")


class XiaohongshuProvider(CaptureProvider):
    name = "xiaohongshu"
    hosts = ("xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com")
    capabilities = ("note-card", "multi-image", "comments", "video-cover")

    def can_handle(self, value: str) -> bool:
        host = (urlparse(str(value or "")).hostname or "").lower()
        return host in self.hosts

    def parse_reference(self, value: str) -> CaptureReference:
        return parse_reference(value)

    async def capture(self, browser: object, reference: CaptureReference, options: CaptureOptions) -> CaptureArtifact:
        page = await open_site_page(browser, reference.url, options.wait_seconds)
        resolved_url = str(getattr(page, "url", "") or reference.url)
        if "xiaohongshu.com" in (urlparse(resolved_url).hostname or "").lower() and hasattr(browser, "apply_cookies"):
            await browser.apply_cookies("xiaohongshu.com")
        resolved_content_id = reference.content_id
        if resolved_url and resolved_url != reference.url:
            try:
                resolved_content_id = parse_reference(resolved_url).content_id
            except InputError:
                pass
        text = await page_text(page)
        check_page_state(text, platform="小红书")
        comments_count = 0
        if options.xhs_comments <= 0:
            await hide_noise(page, (".comments-container", ".comment-container", "[class*=comment]") )
        else:
            try:
                comments_count = int(
                    await page.evaluate(
                        """(limit) => {
                          for (const node of document.querySelectorAll('button,a,[role="button"]')) {
                            const value=(node.innerText||node.textContent||'').trim();
                            if (/展开更多评论|查看更多评论|更多评论/.test(value)) { try { node.click(); } catch (_) {} }
                          }
                          const nodes = Array.from(document.querySelectorAll('.comment-item,.comment-container [class*=comment], [data-comment-id]'));
                          nodes.slice(Math.max(0, limit)).forEach(node => { node.style.display='none'; });
                          return Math.min(limit, nodes.length);
                        }""",
                        max(0, options.xhs_comments),
                    )
                    or 0
                )
            except Exception:
                comments_count = 0
        await hide_noise(page, ("header", "nav", ".login-container", ".sidebar"))
        locator, selector = await first_visible_locator(
            page,
            (
                "#noteContainer",
                ".note-detail",
                ".note-detail-container",
                "section.note-scroller",
                "article",
                "[class*=note-container]",
            ),
            minimum_height=40,
        )
        await wait_for_images(page, f"{selector} img")
        boundary_selectors = ("h1", "h2", "p", "img", "video", "figure", "[class*=comment]")
        image, info = await capture_locator(locator, boundary_selectors=boundary_selectors)
        captured_images = [CapturedImage(image, tuple(info.get("boundaries") or ()), label="image-01")]
        title = (await locator.inner_text(timeout=3000)).strip()[:300]
        image_count = 1
        try:
            image_count = max(1, await locator.locator("img").count())
        except Exception:
            pass
        discovered_frame_count = image_count
        captured_frame_count = 1
        if options.xhs_all_images:
            # Clicking the image is supported by the current web carousel. If
            # a UI version has no clickable gallery, retain the first frame and
            # report that fact instead of fabricating image URLs.
            try:
                gallery = locator.locator("img")
                count = min(await gallery.count(), 12)
                seen: set[str] = {image.hex()}
                frame_ids = await locator.locator("[data-swiper-slide-index]").evaluate_all(
                    "els => [...new Set(els.map(el => el.getAttribute('data-swiper-slide-index')).filter(Boolean))]"
                )
                if not frame_ids:
                    frame_ids = [str(index) for index in range(count)] if await locator.locator(".swiper-pagination-bullet").count() else []
                discovered_frame_count = len(frame_ids) or image_count
                for frame_id in frame_ids[1:]:
                    try:
                        await page.evaluate(
                            """(index) => {
                              const slide = document.querySelector(`[data-swiper-slide-index="${CSS.escape(String(index))}"]`);
                              if (slide) { slide.scrollIntoView({block:'center'}); slide.click(); }
                              const bullet = document.querySelector(`.swiper-pagination-bullet:nth-child(${Number(index)+1})`);
                              if (bullet) bullet.click();
                            }""",
                            frame_id,
                        )
                        await page.wait_for_timeout(180)
                        next_image, next_info = await capture_locator(locator, boundary_selectors=boundary_selectors)
                        marker = next_image.hex()
                        if marker not in seen:
                            seen.add(marker)
                            captured_images.append(
                                CapturedImage(next_image, tuple(next_info.get("boundaries") or ()), label=f"image-{len(captured_images)+1:02d}")
                            )
                            captured_frame_count += 1
                    except Exception:
                        continue
            except Exception:
                pass
        return CaptureArtifact(
            tuple(captured_images),
            "note-card",
            split_long=False,
            metadata={
                "selector": selector,
                "resolved_url": redact_source_url(resolved_url),
                "resolved_content_id": resolved_content_id,
                "title_excerpt": title,
                "image_count": image_count,
                "all_images_requested": bool(options.xhs_all_images),
                "comments_requested": max(0, options.xhs_comments),
                "comments_captured": comments_count,
                "discovered_frame_count": discovered_frame_count,
                "captured_frame_count": captured_frame_count,
                "partial": bool(options.xhs_all_images and captured_frame_count < discovered_frame_count),
                "clip": info,
            },
        )
