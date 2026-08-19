"""Small Playwright helpers shared by built-in providers."""

from __future__ import annotations

import io
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from ..errors import BlockedError, TargetNotFoundError

BLOCKED_MARKERS = (
    "captcha",
    "verify you are human",
    "安全验证",
    "滑动验证",
    "请输入验证码",
    "登录后查看",
    "登录以继续",
)
MISSING_MARKERS = (
    "内容已被删除",
    "微博不存在",
    "该回答不存在",
    "页面不存在",
    "post unavailable",
    "doesn't exist",
    "account suspended",
    "笔记不存在",
)


async def open_site_page(browser: Any, url: str, wait_seconds: float) -> Any:
    page = await browser.new_page()
    host = (urlsplit(url).hostname or "").lower()
    if hasattr(browser, "apply_cookies"):
        await browser.apply_cookies(host)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as exc:
        # A page can still be usable when a platform keeps a long-running
        # request open. Providers inspect body text below before failing.
        if not await _has_any_body_text(page):
            raise TargetNotFoundError(f"页面打开失败: {url}", code="navigation-failed") from exc
    if wait_seconds > 0:
        await page.wait_for_timeout(int(wait_seconds * 1000))
    return page


async def _has_any_body_text(page: Any) -> bool:
    try:
        text = await page.locator("body").inner_text(timeout=2000)
        return bool(str(text).strip())
    except Exception:
        return False


async def page_text(page: Any) -> str:
    try:
        return str(await page.locator("body").inner_text(timeout=5000) or "")
    except Exception:
        return ""


def check_page_state(text: str, *, platform: str) -> None:
    lower = text.lower()
    if any(marker.lower() in lower for marker in BLOCKED_MARKERS):
        raise BlockedError(f"{platform} 页面要求登录、验证或验证码")
    if any(marker.lower() in lower for marker in MISSING_MARKERS):
        raise TargetNotFoundError(f"{platform} 内容不存在、已删除或当前账号无权查看")


async def hide_noise(page: Any, selectors: Iterable[str]) -> None:
    css = ",\n".join(selectors)
    if not css:
        return
    try:
        await page.add_style_tag(content=f"{css} {{ display: none !important; visibility: hidden !important; }}")
    except Exception:
        pass


async def wait_for_images(page: Any, selector: str, timeout_ms: int = 12_000) -> None:
    try:
        await page.evaluate(
            """async ({selector, timeout}) => {
              const nodes = Array.from(document.querySelectorAll(selector));
              for (const node of nodes) node.scrollIntoView({block: 'center', inline: 'nearest'});
              const waitOne = (node) => new Promise(resolve => {
                if (node.complete && node.naturalWidth > 0) return resolve();
                const done = () => { node.removeEventListener('load', done); node.removeEventListener('error', done); resolve(); };
                node.addEventListener('load', done, {once: true});
                node.addEventListener('error', done, {once: true});
                setTimeout(done, timeout);
              });
              await Promise.all(nodes.map(waitOne));
            }""",
            {"selector": selector, "timeout": timeout_ms},
        )
        await page.wait_for_timeout(150)
    except Exception:
        pass


async def first_visible_locator(page: Any, selectors: Iterable[str], *, minimum_height: float = 30) -> tuple[Any, str]:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            for index in range(min(count, 20)):
                candidate = locator.nth(index)
                box = await candidate.bounding_box()
                if box and box["width"] > 180 and box["height"] >= minimum_height:
                    return candidate, selector
        except Exception:
            continue
    raise TargetNotFoundError("无法定位真实内容卡片；已拒绝整页截图", code="card-not-found")


async def capture_locator(locator: Any, *, boundary_selectors: Iterable[str] = ()) -> tuple[bytes, dict[str, Any]]:
    await locator.scroll_into_view_if_needed()
    # Locator.screenshot waits for layout and image readiness itself; an
    # explicit page sleep would require a private Locator.page attribute.
    boundaries = await locator.evaluate(
        """(el, selectors) => {
          const root = el.getBoundingClientRect();
          const values = [];
          for (const selector of selectors) {
            for (const child of el.querySelectorAll(selector)) {
              const rect = child.getBoundingClientRect();
              if (rect.height > 1) values.push({y: Math.round(rect.bottom - root.top), kind: selector});
            }
          }
          return {width: Math.ceil(root.width), height: Math.ceil(Math.max(root.height, el.scrollHeight)), boundaries: values};
        }""",
        list(boundary_selectors),
    )
    screenshot = await locator.screenshot(type="png", animations="disabled", timeout=30_000)
    return screenshot, dict(boundaries or {})


async def capture_locator_banded(
    page: Any,
    locator: Any,
    *,
    boundary_selectors: Iterable[str] = (),
    max_band_height: int = 12_000,
) -> tuple[bytes, dict[str, Any]]:
    """Capture very tall DOM roots in bounded bands and stitch them.

    Playwright/Chrome builds differ in their maximum single-surface height;
    keeping each compositor request <=12,000px avoids blank lower bands while
    retaining the same element-only crop.
    """

    await locator.scroll_into_view_if_needed()
    info = await locator.evaluate(
        """(el, selectors) => {
          const root = el.getBoundingClientRect();
          const boundaries = [];
          for (const selector of selectors) for (const child of el.querySelectorAll(selector)) {
            const rect = child.getBoundingClientRect();
            if (rect.height > 1) boundaries.push({y: Math.round(rect.bottom - root.top), kind: selector});
          }
          return {x: root.left, y: root.top, width: Math.ceil(root.width), height: Math.ceil(Math.max(root.height, el.scrollHeight)), boundaries};
        }""",
        list(boundary_selectors),
    )
    info = dict(info or {})
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if width <= 0 or height <= 0:
        raise TargetNotFoundError("截图区域尺寸无效", code="empty-card")
    if height <= max_band_height:
        return await capture_locator(locator, boundary_selectors=boundary_selectors)
    box = await locator.bounding_box()
    if not box:
        raise TargetNotFoundError("截图区域没有可见边界", code="empty-card")
    images: list[Image.Image] = []
    try:
        for top in range(0, height, max_band_height):
            bottom = min(height, top + max_band_height)
            data = await page.screenshot(
                type="png",
                animations="disabled",
                clip={"x": box["x"], "y": box["y"] + top, "width": width, "height": bottom - top},
            )
            image = Image.open(io.BytesIO(data)).convert("RGBA")
            images.append(image)
        canvas = Image.new("RGBA", (width, sum(image.height for image in images)), (255, 255, 255, 255))
        cursor = 0
        for image in images:
            canvas.paste(image, (0, cursor))
            cursor += image.height
        stream = io.BytesIO()
        canvas.save(stream, format="PNG")
        return stream.getvalue(), info
    finally:
        for image in images:
            image.close()


async def exact_locator_by_marker(page: Any, selectors: Iterable[str], marker: str) -> tuple[Any, str]:
    """Find a card only when its own attributes or links contain ``marker``."""

    matches: list[tuple[Any, str, str]] = []
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            for index in range(min(count, 50)):
                candidate = locator.nth(index)
                box = await candidate.bounding_box()
                if not box or box["width"] <= 180 or box["height"] < 30:
                    continue
                matched = await candidate.evaluate(
                    """(el, marker) => {
                      const attrs = ['id','mid','data-mid','data-id','data-item-id','data-zop','data-zop-answer-id'];
                      if (attrs.some(name => (el.getAttribute(name) || '').includes(marker))) return true;
                      return Array.from(el.querySelectorAll('a[href]')).some(a => (a.getAttribute('href') || '').includes(marker));
                    }""",
                    str(marker),
                )
                if matched:
                    key = await candidate.evaluate(
                        """el => [el.tagName, el.getAttribute('mid'), el.getAttribute('data-mid'), el.getAttribute('data-id'), el.getAttribute('data-item-id'), el.outerHTML.slice(0, 160)].join('|')"""
                    )
                    if not any(existing == key for _, _, existing in matches):
                        matches.append((candidate, selector, key))
        except Exception:
            continue
    if len(matches) == 1:
        return matches[0][0], matches[0][1]
    if len(matches) > 1:
        raise TargetNotFoundError("目标内容匹配多个卡片，无法安全唯一定位；已拒绝截图", code="ambiguous-card")
    raise TargetNotFoundError("无法唯一定位目标内容卡片；已拒绝截取首个候选卡片", code="card-not-found")
