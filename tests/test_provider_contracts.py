from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from social_capture.cli import _parser
from social_capture.errors import TargetNotFoundError
from social_capture.models import CaptureOptions
from social_capture.providers.common import capture_locator_banded, exact_locator_by_marker
from social_capture.providers.douyin.provider import _PROFILE_SCRIPT, DouyinProvider
from social_capture.providers.weibo.provider import WeiboProvider
from social_capture.providers.x.provider import XProvider
from social_capture.providers.xiaohongshu.provider import XiaohongshuProvider
from social_capture.providers.xiaohongshu.provider import parse_reference as parse_xhs
from social_capture.providers.zhihu.provider import _PREPARE_SCRIPT


class ProviderContractTests(unittest.TestCase):
    def test_weibo_contract_requires_exact_marker_and_ambiguous_failure(self):
        source = inspect.getsource(WeiboProvider.capture)
        helper = inspect.getsource(exact_locator_by_marker)
        self.assertIn("exact_locator_by_marker", source)
        self.assertIn("ambiguous-card", helper)
        self.assertNotIn("first_visible_locator", source)

    def test_zhihu_contract_is_answer_exact_question_title_and_lazy_image_safe(self):
        self.assertIn(".ContentItem.AnswerItem", _PREPARE_SCRIPT)
        self.assertIn("/answer/", _PREPARE_SCRIPT)
        self.assertIn("QuestionHeader-title", _PREPARE_SCRIPT)
        self.assertIn("data-actualsrc", _PREPARE_SCRIPT)
        self.assertIn("data-lazy-src", _PREPARE_SCRIPT)
        self.assertIn("data-social-capture-zhihu", _PREPARE_SCRIPT)
        self.assertIn("capture_locator_banded", inspect.getsource(capture_locator_banded))
        self.assertEqual(inspect.signature(capture_locator_banded).parameters["max_band_height"].default, 12_000)
        banded = inspect.getsource(capture_locator_banded)
        self.assertIn("window.scrollTo", banded)
        self.assertIn("document_remaining", banded)

    def test_x_contract_supports_reload_once_and_exact_status_locator(self):
        source = inspect.getsource(XProvider.capture)
        self.assertIn("page.reload", source)
        self.assertEqual(source.count("page.reload"), 1)
        self.assertIn("/status/{reference.content_id}", source)
        self.assertIn('[data-testid="card.wrapper"]', source)
        self.assertNotIn("[data-testid=card.wrapper]", source)
        self.assertIn("2089900346328158261", "2089900346328158261")

    def test_xhs_contract_has_signed_url_and_swiper_frame_handling(self):
        source = inspect.getsource(XiaohongshuProvider.capture)
        self.assertIn("data-swiper-slide-index", source)
        self.assertIn("swiper-pagination-bullet", source)
        self.assertIn("xsec_token", inspect.getsource(parse_xhs))
        self.assertIn("comments", source)
        self.assertIn("resolved_content_id", source)

    def test_douyin_contract_requires_real_profile_and_keeps_two_site_rows(self):
        self.assertIn('[data-e2e="user-info"]', _PROFILE_SCRIPT)
        self.assertIn("profile_found", _PROFILE_SCRIPT)
        self.assertIn("profile_fields", _PROFILE_SCRIPT)
        self.assertIn("stats_found", _PROFILE_SCRIPT)
        self.assertIn("data-social-capture-douyin-profile", _PROFILE_SCRIPT)
        self.assertIn("gridTemplateColumns", _PROFILE_SCRIPT)
        self.assertNotIn("'header'", _PROFILE_SCRIPT)
        self.assertNotIn('"header"', _PROFILE_SCRIPT)
        self.assertNotIn("if (!profile && !chosen.length)", _PROFILE_SCRIPT)
        self.assertIn("medianHeight", _PROFILE_SCRIPT)
        self.assertIn("seenCards", _PROFILE_SCRIPT)
        self.assertIn("map(value => Math.round(value * 10) / 10)", _PROFILE_SCRIPT)
        self.assertNotIn("'li'", _PROFILE_SCRIPT)
        capture_source = inspect.getsource(__import__("social_capture.providers.douyin.provider", fromlist=["DouyinProvider"]).DouyinProvider.capture)
        self.assertIn("requested_rows", capture_source)
        self.assertIn("partial", capture_source)
        self.assertIn("profile_found", capture_source)
        self.assertIn("profile_fields", capture_source)
        self.assertIn("stats_found", capture_source)
        args = _parser().parse_args(["capture", "abc", "--output-dir", "out", "--douyin-rows", "2"])
        self.assertEqual(args.douyin_rows, 2)


class DouyinCaptureBehaviorTests(unittest.IsolatedAsyncioTestCase):
    class _Locator:
        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def screenshot(self, **_kwargs):
            return b"png"

    class _Page:
        def __init__(self, result):
            self.result = result

        async def evaluate(self, *_args):
            return self.result

        def locator(self, _selector):
            return DouyinCaptureBehaviorTests._Locator()

    def _options(self):
        return CaptureOptions(output_dir=Path("out"), douyin_rows=2)

    async def test_profile_missing_fails_even_when_posts_are_present(self):
        page = self._Page({"ok": False, "code": "profile-not-found", "profile_found": False, "cards": 12})
        reference = DouyinProvider().parse_reference("sec_uid")
        with patch("social_capture.providers.douyin.provider.open_site_page", new=AsyncMock(return_value=page)), patch(
            "social_capture.providers.douyin.provider.page_text", new=AsyncMock(return_value="红姐")
        ), self.assertRaises(TargetNotFoundError) as raised:
            await DouyinProvider().capture(object(), reference, self._options())
        self.assertEqual(raised.exception.code, "profile-not-found")

    async def test_success_exposes_profile_acceptance_metadata(self):
        page = self._Page(
            {
                "ok": True,
                "profile_found": True,
                "profile_fields": ["nickname", "account_id", "avatar", "stats"],
                "nickname": "示例账号",
                "account_id": "123456",
                "stats_found": True,
                "rows": 2,
                "cards": 12,
                "columns": 6,
                "partial": False,
                "clip": {"x": 0, "y": 0, "width": 100, "height": 100},
                "selector": '[data-social-capture-douyin="true"]',
            }
        )
        reference = DouyinProvider().parse_reference("sec_uid")
        with patch("social_capture.providers.douyin.provider.open_site_page", new=AsyncMock(return_value=page)), patch(
            "social_capture.providers.douyin.provider.page_text", new=AsyncMock(return_value="示例账号")
        ):
            artifact = await DouyinProvider().capture(object(), reference, self._options())
        self.assertEqual(artifact.metadata["nickname"], "示例账号")
        self.assertTrue(artifact.metadata["profile_found"])
        self.assertTrue(artifact.metadata["stats_found"])
        self.assertEqual(artifact.metadata["columns"], 6)


if __name__ == "__main__":
    unittest.main()
