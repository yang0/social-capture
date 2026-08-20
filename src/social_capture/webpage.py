"""Capture a user-specified webpage without platform-specific assumptions."""

from __future__ import annotations

import hashlib
import io
import re
import time
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from .auth import redact_source_url, redact_text
from .errors import BrowserError, ImageError, InputError, TargetNotFoundError
from .models import CaptureArtifact, CapturedImage, CaptureReference
from .output.writer import safe_filename

WEBPAGE_MODES = ("viewport", "full-page", "element")
DEFAULT_VIEWPORT = (1440, 900)
_VIEWPORT_PATTERN = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$")
_MAX_SCREENSHOT_DIMENSION = 32_767
_MAX_SCREENSHOT_PIXELS = 268_000_000

# Full-page screenshots need a small, bounded warm-up pass because browsers
# normally only materialise ``loading=lazy`` images near the viewport.  Keep
# these limits deliberately conservative: this helper must never turn into an
# infinite-scroll crawler or wait forever on an unreachable image/font.
_FULL_PAGE_SCROLL_STEP_RATIO = 0.9
_FULL_PAGE_MAX_SCROLL_STEPS = 64
_FULL_PAGE_MAX_ROUNDS = 4
_FULL_PAGE_SCROLL_SETTLE_MS = 120
_FULL_PAGE_ASSET_POLL_MS = 120
_FULL_PAGE_WARMUP_MAX_WAIT_MS = 10_000

_LAZY_ASSET_STATUS_SCRIPT = """() => {
  const images = Array.from(document.images || []);
  let loaded = 0;
  let failed = 0;
  let pending = 0;
  for (const image of images) {
    if (!image.complete) {
      pending += 1;
    } else if (image.naturalWidth > 0 || image.naturalHeight > 0) {
      loaded += 1;
    } else {
      failed += 1;
    }
  }
  return {
    image_count: images.length,
    loaded,
    failed,
    pending,
    fonts_pending: Boolean(document.fonts && document.fonts.status !== 'loaded'),
  };
}"""


def parse_viewport(value: str | None) -> tuple[int, int]:
    """Parse a WIDTHxHEIGHT value used for deterministic webpage captures."""

    raw = str(value or "").strip()
    match = _VIEWPORT_PATTERN.fullmatch(raw)
    if not match:
        raise InputError("--viewport 必须是正整数尺寸，例如 1440x900", code="invalid-viewport")
    width, height = (int(part) for part in match.groups())
    if width <= 0 or height <= 0:
        raise InputError("--viewport 必须是正整数尺寸，例如 1440x900", code="invalid-viewport")
    if width > _MAX_SCREENSHOT_DIMENSION or height > _MAX_SCREENSHOT_DIMENSION:
        raise InputError(
            f"--viewport 不能超过 {_MAX_SCREENSHOT_DIMENSION}x{_MAX_SCREENSHOT_DIMENSION}",
            code="invalid-viewport",
        )
    if width * height > _MAX_SCREENSHOT_PIXELS:
        raise InputError("--viewport 像素总量超出 Chrome 安全上限", code="invalid-viewport")
    return width, height


def validate_webpage_url(value: str) -> str:
    """Validate a concrete HTTP(S) URL while preserving its navigation value."""

    raw = str(value or "").strip()
    if not raw or any(char.isspace() for char in raw):
        raise InputError("网页 URL 不能为空且不能包含空格", code="invalid-url")
    try:
        parts = urlsplit(raw)
        hostname = parts.hostname
    except ValueError as exc:
        raise InputError("网页 URL 格式无效", code="invalid-url") from exc
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        raise InputError("网页截图只接受 HTTP(S) URL", code="invalid-url")
    if parts.username or parts.password:
        raise InputError("网页 URL 不得包含账号或密码", code="invalid-url")
    return raw


def webpage_reference(url: str) -> CaptureReference:
    """Build a safe, deterministic manifest reference for a webpage URL."""

    raw = validate_webpage_url(url)
    safe_url = redact_source_url(raw)
    hostname = (urlsplit(raw).hostname or "page").lower()
    digest = hashlib.sha256(safe_url.encode("utf-8")).hexdigest()[:12]
    content_id = f"{safe_filename(hostname, fallback='page', limit=40)}-{digest}"
    return CaptureReference(
        platform="webpage",
        content_id=content_id,
        url=safe_url,
        input_value=safe_url,
        content_type="webpage",
    )


async def _body_has_text(page: Any) -> bool:
    try:
        text = await page.locator("body").inner_text(timeout=2_000)
        return bool(str(text or "").strip())
    except Exception:
        return False


async def _set_viewport(page: Any, viewport: tuple[int, int]) -> None:
    setter = getattr(page, "set_viewport_size", None)
    if not callable(setter):
        # Real Playwright pages always expose this method. Keeping the small
        # compatibility fallback makes the capture helper usable with narrow
        # browser fakes in integrations and tests.
        return
    try:
        await setter({"width": viewport[0], "height": viewport[1]})
    except Exception as exc:
        raise BrowserError("无法设置浏览器视口尺寸", code="viewport-failed") from exc


async def _document_size(page: Any) -> tuple[int, int] | None:
    try:
        value = await page.evaluate(
            """() => {
              const root = document.documentElement;
              const body = document.body;
              return {
                width: Math.max(root ? root.scrollWidth : 0, body ? body.scrollWidth : 0),
                height: Math.max(root ? root.scrollHeight : 0, body ? body.scrollHeight : 0),
              };
            }"""
        )
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    try:
        width, height = int(value.get("width") or 0), int(value.get("height") or 0)
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _full_page_scroll_positions(
    document_height: int,
    viewport_height: int,
    *,
    start_position: int = 0,
    max_steps: int | None = None,
) -> list[int]:
    """Return finite positions for an uncovered range of the document."""

    max_scroll = max(0, int(document_height) - max(1, int(viewport_height)))
    first_position = min(max_scroll, max(0, int(start_position)))
    step = max(1, int(max(1, viewport_height) * _FULL_PAGE_SCROLL_STEP_RATIO))
    positions = list(range(first_position, max_scroll + 1, step))
    if not positions:
        positions = [max_scroll]
    if positions[-1] != max_scroll:
        positions.append(max_scroll)
    limit = _FULL_PAGE_MAX_SCROLL_STEPS if max_steps is None else max(1, int(max_steps))
    if len(positions) <= limit:
        return positions
    if limit == 1:
        return [max_scroll]

    # Very tall ranges still get a bounded pass. Preserve the first position
    # and the exact requested bottom; distribute the remaining points across
    # the range without expanding the range while this pass is running.
    last_index = len(positions) - 1
    sample_count = limit
    selected = [positions[(index * last_index) // (sample_count - 1)] for index in range(sample_count)]
    return list(dict.fromkeys(selected))


def _empty_lazy_asset_status() -> dict[str, int | bool]:
    return {
        "image_count": 0,
        "loaded": 0,
        "failed": 0,
        "pending": 0,
        "fonts_pending": False,
    }


async def _lazy_asset_status(page: Any) -> dict[str, int | bool] | None:
    """Read image/font state without making a failed external asset fatal."""

    try:
        value = await page.evaluate(_LAZY_ASSET_STATUS_SCRIPT)
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    try:
        image_count = max(0, int(value.get("image_count") or 0))
        loaded = max(0, int(value.get("loaded") or 0))
        failed = max(0, int(value.get("failed") or 0))
        pending = max(0, int(value.get("pending") or 0))
    except (TypeError, ValueError):
        return None
    return {
        "image_count": image_count,
        "loaded": loaded,
        "failed": failed,
        "pending": pending,
        "fonts_pending": bool(value.get("fonts_pending")),
    }


async def _wait_for_page_timeout(page: Any, milliseconds: int) -> bool:
    if milliseconds <= 0:
        return True
    waiter = getattr(page, "wait_for_timeout", None)
    if callable(waiter):
        await waiter(int(milliseconds))
        return True
    return False


async def _wait_for_lazy_assets(
    page: Any,
    status: dict[str, int | bool] | None,
    deadline: float,
) -> tuple[dict[str, int | bool], bool]:
    """Poll current image/font state until settled or the shared budget ends."""

    latest = await _lazy_asset_status(page)
    if latest is None:
        return status or _empty_lazy_asset_status(), True
    status = latest
    while int(status["pending"]) > 0 or bool(status["fonts_pending"]):
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            return status, True
        if not await _wait_for_page_timeout(page, min(_FULL_PAGE_ASSET_POLL_MS, remaining_ms)):
            return status, True
        latest = await _lazy_asset_status(page)
        if latest is None:
            return status, True
        status = latest
    return status, False


async def _warm_full_page(page: Any, viewport_height: int) -> dict[str, int | bool]:
    """Trigger lazy assets over a finite, height-aware range within a hard budget."""

    dimensions = await _document_size(page)
    initial_height = int(dimensions[1]) if dimensions else 0
    initial_width = int(dimensions[0]) if dimensions else 0
    deadline = time.monotonic() + (_FULL_PAGE_WARMUP_MAX_WAIT_MS / 1000)
    timed_out = False
    current_height = initial_height
    current_width = initial_width
    final_height = initial_height
    final_width = initial_width
    previous_max_scroll = 0
    next_start_position = 0
    executed_steps = 0
    rounds = 0
    status: dict[str, int | bool] | None = None

    while rounds < _FULL_PAGE_MAX_ROUNDS and executed_steps < _FULL_PAGE_MAX_SCROLL_STEPS:
        rounds += 1
        if current_height > 0:
            positions = _full_page_scroll_positions(
                current_height,
                viewport_height,
                start_position=next_start_position,
                max_steps=_FULL_PAGE_MAX_SCROLL_STEPS - executed_steps,
            )
        else:
            positions = [0]
        if not positions:
            break

        reached_budget = False
        for position in positions:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                timed_out = True
                reached_budget = True
                break
            try:
                await page.evaluate("y => window.scrollTo(0, y)", position)
            except Exception:
                # Scrolling is a best-effort trigger. Let Playwright's final
                # screenshot decide whether the page itself is still capturable.
                timed_out = True
                reached_budget = True
                break
            executed_steps += 1
            await _wait_for_page_timeout(page, min(_FULL_PAGE_SCROLL_SETTLE_MS, remaining_ms))

        status, asset_timed_out = await _wait_for_lazy_assets(page, status, deadline)
        timed_out = timed_out or asset_timed_out

        measured = await _document_size(page)
        if measured:
            current_width, measured_height = int(measured[0]), int(measured[1])
            final_width = current_width
            final_height = measured_height
        else:
            measured_height = current_height
        if reached_budget or timed_out:
            break
        if measured_height <= current_height:
            break

        previous_max_scroll = max(0, current_height - max(1, int(viewport_height)))
        current_height = measured_height
        # Start the next pass after the previous bottom. The step-sized gap
        # avoids replaying old positions while the final position guarantees
        # coverage of the newly appended range.
        new_max_scroll = max(0, current_height - max(1, int(viewport_height)))
        next_start_position = min(new_max_scroll, previous_max_scroll + max(1, int(viewport_height * _FULL_PAGE_SCROLL_STEP_RATIO)))

    final_warmup_height = final_height or current_height

    # Always return to the top before the actual full-page capture. The loop
    # above only remeasures a bounded number of rounds and never follows
    # unbounded page growth.
    try:
        await page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        timed_out = True

    return {
        "lazy_load_initial_width": initial_width,
        "lazy_load_initial_height": initial_height,
        "lazy_load_final_width": final_width,
        "lazy_load_final_height": final_warmup_height,
        "lazy_load_scroll_steps": executed_steps,
        "image_count": int(status["image_count"]),
        "loaded": int(status["loaded"]),
        "failed": int(status["failed"]),
        "pending": int(status["pending"]),
        "lazy_load_timed_out": timed_out,
        "initial_height": initial_height,
        "final_warmup_height": final_warmup_height,
        "rounds": rounds,
        "executed_steps": executed_steps,
        "timed_out": timed_out,
    }


def _check_screenshot_size(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or width > _MAX_SCREENSHOT_DIMENSION
        or height > _MAX_SCREENSHOT_DIMENSION
        or width * height > _MAX_SCREENSHOT_PIXELS
    ):
        raise ImageError("截图尺寸超出 Chrome 安全上限", code="screenshot-too-large")


def _image_info(data: bytes) -> tuple[int, int, str]:
    image: Image.Image | None = None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        width, height = int(image.width), int(image.height)
    except Exception as exc:
        raise ImageError("浏览器返回的数据不是有效 PNG/图片", code="invalid-image") from exc
    finally:
        if image is not None:
            image.close()
    _check_screenshot_size(width, height)
    return width, height, hashlib.sha256(data).hexdigest()


async def _visible_selector_match(page: Any, selector: str) -> Any:
    try:
        locator = page.locator(selector)
        count = await locator.count()
    except Exception as exc:
        raise InputError("--selector 不是有效的 CSS 选择器", code="invalid-selector") from exc

    visible: list[Any] = []
    for index in range(count):
        candidate = locator.nth(index)
        try:
            is_visible: bool | None = bool(await candidate.is_visible())
        except Exception:
            # Some compatible locator implementations only expose a bounding
            # box. A positive box is the visibility signal in that case.
            is_visible = None
        try:
            box = await candidate.bounding_box()
        except Exception:
            box = None
        if is_visible is False or (is_visible is None and box is None):
            continue
        if box is not None and (float(box.get("width", 0)) <= 0 or float(box.get("height", 0)) <= 0):
            continue
        visible.append(candidate)

    if not visible:
        raise TargetNotFoundError("--selector 没有匹配到可见元素", code="selector-not-found")
    if len(visible) != 1:
        raise TargetNotFoundError(
            "--selector 必须唯一匹配一个可见元素",
            code="selector-ambiguous",
        )
    return visible[0]


async def capture_webpage(
    browser: Any,
    url: str,
    *,
    mode: str = "viewport",
    selector: str | None = None,
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    wait_seconds: float = 3.0,
) -> CaptureArtifact:
    """Navigate to ``url`` and return one PNG for the requested capture mode."""

    raw_url = validate_webpage_url(url)
    if mode not in WEBPAGE_MODES:
        raise InputError(f"截图模式必须是 {', '.join(WEBPAGE_MODES)}", code="invalid-mode")
    if mode == "element" and not str(selector or "").strip():
        raise InputError("element 模式必须提供 --selector", code="selector-required")
    if mode != "element" and selector is not None:
        raise InputError("--selector 只适用于 element 模式", code="selector-not-allowed")
    try:
        viewport_width, viewport_height = int(viewport[0]), int(viewport[1])
    except (IndexError, TypeError, ValueError):
        raise InputError("网页截图视口尺寸无效", code="invalid-viewport") from None
    if (
        viewport_width <= 0
        or viewport_height <= 0
        or viewport_width > _MAX_SCREENSHOT_DIMENSION
        or viewport_height > _MAX_SCREENSHOT_DIMENSION
        or viewport_width * viewport_height > _MAX_SCREENSHOT_PIXELS
    ):
        raise InputError("网页截图视口尺寸无效", code="invalid-viewport")
    viewport = (viewport_width, viewport_height)

    safe_url = redact_source_url(raw_url)
    page = await browser.new_page()
    await _set_viewport(page, viewport)
    try:
        await page.goto(raw_url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as exc:
        if not await _body_has_text(page):
            raise TargetNotFoundError(f"页面打开失败: {safe_url}", code="navigation-failed") from exc
    if wait_seconds > 0:
        waiter = getattr(page, "wait_for_timeout", None)
        if callable(waiter):
            await waiter(int(wait_seconds * 1000))

    if mode == "element":
        target = await _visible_selector_match(page, str(selector))
        try:
            await target.scroll_into_view_if_needed()
            data = await target.screenshot(type="png", animations="disabled", timeout=30_000)
        except TargetNotFoundError:
            raise
        except Exception as exc:
            raise ImageError("元素截图执行失败", code="screenshot-failed") from exc
    else:
        full_page_metadata: dict[str, int | bool] = {}
        if mode == "full-page":
            full_page_metadata = await _warm_full_page(page, viewport[1])
            check_width = full_page_metadata["lazy_load_final_width"] or full_page_metadata["lazy_load_initial_width"]
            check_height = full_page_metadata["final_warmup_height"] or full_page_metadata["lazy_load_initial_height"]
            if check_width and check_height:
                _check_screenshot_size(
                    int(check_width),
                    int(check_height),
                )
        try:
            data = await page.screenshot(
                type="png",
                animations="disabled",
                full_page=mode == "full-page",
            )
        except Exception as exc:
            raise ImageError("网页截图执行失败", code="screenshot-failed") from exc

    width, height, digest = _image_info(data)
    final_url = safe_url
    try:
        final_url = redact_source_url(str(page.url or raw_url))
    except Exception:
        pass
    try:
        title = redact_text(str(await page.title() or ""))
    except Exception:
        title = ""
    metadata: dict[str, Any] = {
        "viewport": {"width": viewport[0], "height": viewport[1]},
        "final_url": final_url,
        "title": title,
        "width": width,
        "height": height,
        "sha256": digest,
    }
    if mode == "full-page":
        metadata.update(full_page_metadata)
    if mode == "element":
        metadata["selector"] = str(selector)
    return CaptureArtifact(
        images=(CapturedImage(data),),
        capture_mode=mode,
        split_long=False,
        metadata=metadata,
    )


__all__ = [
    "DEFAULT_VIEWPORT",
    "WEBPAGE_MODES",
    "capture_webpage",
    "parse_viewport",
    "validate_webpage_url",
    "webpage_reference",
]
