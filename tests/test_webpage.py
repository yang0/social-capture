from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

import social_capture.webpage as webpage_module
from social_capture.browser import BrowserSession
from social_capture.cli import main
from social_capture.errors import BrowserError, InputError, TargetNotFoundError
from social_capture.models import CaptureArtifact, CapturedImage, CaptureOptions
from social_capture.output import OutputWriter
from social_capture.webpage import (
    capture_webpage,
    parse_viewport,
    validate_webpage_url,
    webpage_reference,
)


def _png(width: int = 120, height: int = 80) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(stream, format="PNG")
    return stream.getvalue()


class _Element:
    def __init__(self, *, visible: bool = True):
        self.visible = visible
        self.scrolled = False
        self.screenshot_kwargs: dict[str, object] = {}

    async def is_visible(self) -> bool:
        return self.visible

    async def bounding_box(self) -> dict[str, float]:
        return {"x": 0, "y": 0, "width": 200, "height": 100}

    async def scroll_into_view_if_needed(self) -> None:
        self.scrolled = True

    async def screenshot(self, **kwargs: object) -> bytes:
        self.screenshot_kwargs = kwargs
        return _png()


class _Locator:
    def __init__(self, elements: list[_Element]):
        self.elements = elements

    async def count(self) -> int:
        return len(self.elements)

    def nth(self, index: int) -> _Element:
        return self.elements[index]


class _Page:
    def __init__(self, elements: list[_Element] | None = None):
        self.elements = elements or []
        self.viewport: dict[str, int] | None = None
        self.goto_args: tuple[object, ...] = ()
        self.screenshot_kwargs: dict[str, object] = {}
        self.url = "https://example.com/final?token=secret&keep=1"

    async def set_viewport_size(self, viewport: dict[str, int]) -> None:
        self.viewport = viewport

    async def goto(self, *args: object, **kwargs: object) -> None:
        self.goto_args = args

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def locator(self, selector: str) -> _Locator:
        if selector == "body":
            return _Locator([])
        return _Locator(self.elements)

    async def evaluate(self, _script: str) -> dict[str, int]:
        return {"width": 1200, "height": 1000}

    async def screenshot(self, **kwargs: object) -> bytes:
        self.screenshot_kwargs = kwargs
        return _png()

    async def title(self) -> str:
        return "Example"


class _LazyPage(_Page):
    def __init__(
        self,
        *,
        document_width: int = 1200,
        document_height: int = 1000,
        statuses: list[dict[str, object]] | None = None,
    ):
        super().__init__()
        self.document_width = document_width
        self.document_height = document_height
        self.statuses = statuses or [
            {"image_count": 2, "loaded": 2, "failed": 0, "pending": 0, "fonts_pending": False}
        ]
        self.status_reads = 0
        self.scroll_positions: list[int] = []
        self.waits: list[int] = []

    async def evaluate(self, script: str, *args: object) -> object:
        if "window.scrollTo" in script:
            self.scroll_positions.append(int(args[0]) if args else 0)
            return None
        if "document.images" in script:
            status = self.statuses[min(self.status_reads, len(self.statuses) - 1)]
            self.status_reads += 1
            return status
        return {"width": self.document_width, "height": self.document_height}

    async def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


class _GrowingLazyPage(_LazyPage):
    def __init__(self) -> None:
        super().__init__(
            document_height=2_239,
            statuses=[
                {"image_count": 47, "loaded": 35, "failed": 12, "pending": 0, "fonts_pending": False},
                {"image_count": 67, "loaded": 47, "failed": 20, "pending": 0, "fonts_pending": False},
            ],
        )
        self.growth_heights = [3_673, 3_673]
        self.growth_index = 0
        self.viewport_height = 900

    async def set_viewport_size(self, viewport: dict[str, int]) -> None:
        await super().set_viewport_size(viewport)
        self.viewport_height = viewport["height"]

    async def evaluate(self, script: str, *args: object) -> object:
        if "window.scrollTo" in script and args:
            position = int(args[0])
            self.scroll_positions.append(position)
            if position >= self.document_height - self.viewport_height and self.growth_index < len(self.growth_heights):
                self.document_height = self.growth_heights[self.growth_index]
                self.growth_index += 1
            return None
        return await super().evaluate(script, *args)


class _Browser:
    def __init__(self, page: _Page):
        self.page = page

    async def new_page(self) -> _Page:
        return self.page


class _Session:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class WebpageCaptureTests(unittest.TestCase):
    def test_validation_and_reference_redact_secrets(self):
        self.assertEqual(parse_viewport("1280x800"), (1280, 800))
        self.assertEqual(validate_webpage_url("https://example.com/a"), "https://example.com/a")
        with self.assertRaises(InputError):
            validate_webpage_url("file:///etc/passwd")
        with self.assertRaises(InputError):
            parse_viewport("32767x32767")
        reference = webpage_reference("https://example.com/a?token=secret&keep=1")
        self.assertNotIn("secret", reference.url)
        self.assertIn("keep=1", reference.url)

    def test_viewport_and_full_page_modes_use_one_png(self):
        for mode, full_page in (("viewport", False), ("full-page", True)):
            page = _Page()
            artifact = asyncio.run(
                capture_webpage(
                    _Browser(page),
                    "https://example.com",
                    mode=mode,
                    viewport=(1280, 800),
                    wait_seconds=0,
                )
            )
            self.assertEqual(page.viewport, {"width": 1280, "height": 800})
            self.assertEqual(page.screenshot_kwargs["full_page"], full_page)
            self.assertEqual(artifact.capture_mode, mode)
            self.assertEqual(len(artifact.images), 1)

    def test_full_page_warms_lazy_assets_with_bounded_scroll_and_returns_top(self):
        page = _LazyPage(
            document_height=30_000,
            statuses=[
                {"image_count": 3, "loaded": 1, "failed": 1, "pending": 1, "fonts_pending": True},
                {"image_count": 3, "loaded": 2, "failed": 1, "pending": 0, "fonts_pending": False},
            ],
        )
        artifact = asyncio.run(
            capture_webpage(
                _Browser(page),
                "https://example.com",
                mode="full-page",
                viewport=(1200, 300),
                wait_seconds=0,
            )
        )

        self.assertLessEqual(
            len(page.scroll_positions) - 1,
            webpage_module._FULL_PAGE_MAX_SCROLL_STEPS,
        )
        self.assertEqual(page.scroll_positions[-1], 0)
        self.assertEqual(page.scroll_positions[-2], 29_700)
        self.assertGreaterEqual(len(page.waits), 1)
        self.assertEqual(artifact.metadata["image_count"], 3)
        self.assertEqual(artifact.metadata["loaded"], 2)
        self.assertEqual(artifact.metadata["failed"], 1)
        self.assertEqual(artifact.metadata["pending"], 0)
        self.assertFalse(artifact.metadata["lazy_load_timed_out"])

    def test_full_page_rescans_finite_new_height_without_repeating_old_positions(self):
        page = _GrowingLazyPage()
        artifact = asyncio.run(
            capture_webpage(
                _Browser(page),
                "https://example.com",
                mode="full-page",
                viewport=(1200, 900),
                wait_seconds=0,
            )
        )

        scan_positions = page.scroll_positions[:-1]
        self.assertEqual(scan_positions, [0, 810, 1_339, 2_149, 2_773])
        self.assertEqual(len(scan_positions), len(set(scan_positions)))
        self.assertEqual(page.scroll_positions[-1], 0)
        self.assertEqual(artifact.metadata["initial_height"], 2_239)
        self.assertEqual(artifact.metadata["final_warmup_height"], 3_673)
        self.assertEqual(artifact.metadata["rounds"], 2)
        self.assertEqual(artifact.metadata["executed_steps"], 5)
        self.assertEqual(artifact.metadata["image_count"], 67)
        self.assertEqual(artifact.metadata["loaded"], 47)
        self.assertEqual(artifact.metadata["failed"], 20)
        self.assertFalse(artifact.metadata["timed_out"])

    def test_full_page_stops_after_max_growth_rounds(self):
        page = _GrowingLazyPage()
        page.growth_heights = [3_673, 5_000, 6_500, 8_000, 9_500]
        artifact = asyncio.run(
            capture_webpage(
                _Browser(page),
                "https://example.com",
                mode="full-page",
                viewport=(1200, 900),
                wait_seconds=0,
            )
        )

        self.assertEqual(artifact.metadata["rounds"], webpage_module._FULL_PAGE_MAX_ROUNDS)
        self.assertLessEqual(
            artifact.metadata["executed_steps"],
            webpage_module._FULL_PAGE_MAX_SCROLL_STEPS,
        )
        self.assertEqual(page.scroll_positions[-1], 0)

    def test_viewport_does_not_run_full_page_scroll_warmup(self):
        page = _LazyPage(statuses=[])
        asyncio.run(
            capture_webpage(
                _Browser(page),
                "https://example.com",
                mode="viewport",
                wait_seconds=0,
            )
        )
        self.assertEqual(page.scroll_positions, [])
        self.assertEqual(page.status_reads, 0)

    def test_full_page_asset_timeout_is_recorded_and_does_not_fail_capture(self):
        page = _LazyPage(
            statuses=[
                {"image_count": 1, "loaded": 0, "failed": 0, "pending": 1, "fonts_pending": False}
            ]
        )
        with patch.object(webpage_module, "_FULL_PAGE_WARMUP_MAX_WAIT_MS", 10), patch.object(
            webpage_module, "_FULL_PAGE_SCROLL_SETTLE_MS", 1
        ), patch.object(webpage_module, "_FULL_PAGE_ASSET_POLL_MS", 1):
            artifact = asyncio.run(
                capture_webpage(
                    _Browser(page),
                    "https://example.com",
                    mode="full-page",
                    viewport=(1200, 800),
                    wait_seconds=0,
                )
            )

        self.assertTrue(artifact.metadata["lazy_load_timed_out"])
        self.assertEqual(artifact.metadata["image_count"], 1)
        self.assertEqual(artifact.metadata["pending"], 1)
        self.assertEqual(page.scroll_positions[-1], 0)

    def test_element_mode_requires_one_visible_match(self):
        hidden = _Element(visible=False)
        visible = _Element(visible=True)
        page = _Page([hidden, visible])
        artifact = asyncio.run(
            capture_webpage(
                _Browser(page),
                "https://example.com",
                mode="element",
                selector="main article",
                wait_seconds=0,
            )
        )
        self.assertEqual(artifact.capture_mode, "element")

        with self.assertRaises(TargetNotFoundError) as context:
            asyncio.run(
                capture_webpage(
                    _Browser(_Page([_Element(), _Element()])),
                    "https://example.com",
                    mode="element",
                    selector="main article",
                    wait_seconds=0,
                )
            )
        self.assertEqual(context.exception.code, "selector-ambiguous")

    def test_webpage_manifest_has_mode_dimensions_hash_and_redacted_url(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            reference = webpage_reference("https://example.com/a?token=secret&keep=1")
            data = _png()
            artifact = CaptureArtifact(
                (CapturedImage(data),),
                "viewport",
                metadata={
                    "viewport": {"width": 1440, "height": 900},
                    "final_url": "https://example.com/final?token=secret&keep=1",
                    "title": "Example",
                },
            )
            writer = OutputWriter(output)
            writer.preflight()
            item = writer.write_artifact(reference, artifact, CaptureOptions(output))
            manifest = writer.write_manifest([item], extra={"capture_mode": "viewport"})
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            row = payload["items"][0]
            self.assertEqual(row["capture_mode"], "viewport")
            self.assertEqual(row["parts"][0]["mode"], "viewport")
            self.assertEqual(row["width"], 120)
            self.assertEqual(row["height"], 80)
            self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
            self.assertNotIn("secret", json.dumps(payload))

    def test_cli_writes_webpage_failure_manifest_for_unavailable_navigation(self):
        async def fail_capture(*_args: object, **_kwargs: object) -> CaptureArtifact:
            raise TargetNotFoundError("页面打开失败", code="navigation-failed")

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            with patch("social_capture.cli.BrowserSession", return_value=_Session()), patch(
                "social_capture.cli.capture_webpage", new=fail_capture
            ):
                code = main(
                    [
                        "webpage",
                        "https://example.com/a?token=secret",
                        "--output-dir",
                        str(output),
                        "--wait",
                        "0",
                    ]
                )
            self.assertEqual(code, 2)
            payload = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["items"][0]["platform"], "webpage")
            self.assertNotIn("secret", json.dumps(payload))


class WebpageBrowserFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_temporary_chrome_fallback_is_explicit_and_cleaned_up(self):
        strict = BrowserSession("http://127.0.0.1:1")
        with patch.object(
            strict, "_connect", new=AsyncMock(side_effect=BrowserError("unavailable"))
        ), patch("social_capture.browser.session.TemporaryChrome") as temporary_class:
            with self.assertRaises(BrowserError):
                await strict.__aenter__()
            temporary_class.assert_not_called()

        fallback = BrowserSession("http://127.0.0.1:1", allow_temporary=True)
        temporary = MagicMock()
        temporary.start.return_value = "http://127.0.0.1:2"
        with patch.object(
            fallback,
            "_connect",
            new=AsyncMock(side_effect=[BrowserError("unavailable"), None]),
        ), patch("social_capture.browser.session.TemporaryChrome", return_value=temporary):
            self.assertIs(await fallback.__aenter__(), fallback)
            await fallback.__aexit__(None, None, None)
        temporary.start.assert_called_once()
        temporary.close.assert_called_once()

    async def test_failed_temporary_connection_is_cleaned_up(self):
        fallback = BrowserSession("http://127.0.0.1:1", allow_temporary=True)
        temporary = MagicMock()
        temporary.start.return_value = "http://127.0.0.1:2"
        with patch.object(
            fallback,
            "_connect",
            new=AsyncMock(
                side_effect=[BrowserError("unavailable"), BrowserError("fallback unavailable")]
            ),
        ), patch(
            "social_capture.browser.session.TemporaryChrome", return_value=temporary
        ), self.assertRaises(BrowserError):
            await fallback.__aenter__()
        temporary.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
