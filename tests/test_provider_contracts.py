from __future__ import annotations

import inspect
import unittest

from social_capture.cli import _parser
from social_capture.providers.common import capture_locator_banded, exact_locator_by_marker
from social_capture.providers.douyin.provider import _PROFILE_SCRIPT
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

    def test_x_contract_supports_reload_once_and_exact_status_locator(self):
        source = inspect.getsource(XProvider.capture)
        self.assertIn("page.reload", source)
        self.assertEqual(source.count("page.reload"), 1)
        self.assertIn("/status/{reference.content_id}", source)
        self.assertIn("2089900346328158261", "2089900346328158261")

    def test_xhs_contract_has_signed_url_and_swiper_frame_handling(self):
        source = inspect.getsource(XiaohongshuProvider.capture)
        self.assertIn("data-swiper-slide-index", source)
        self.assertIn("swiper-pagination-bullet", source)
        self.assertIn("xsec_token", inspect.getsource(parse_xhs))
        self.assertIn("comments", source)
        self.assertIn("resolved_content_id", source)

    def test_douyin_contract_limits_grid_and_records_partial_rows(self):
        self.assertIn("cards.slice(0, 12)", _PROFILE_SCRIPT)
        self.assertIn("medianHeight", _PROFILE_SCRIPT)
        self.assertIn("user-post-list", _PROFILE_SCRIPT)
        self.assertIn("seenRects", _PROFILE_SCRIPT)
        self.assertIn("map(value => Math.round(value * 10) / 10)", _PROFILE_SCRIPT)
        self.assertNotIn("'li'", _PROFILE_SCRIPT)
        capture_source = inspect.getsource(__import__("social_capture.providers.douyin.provider", fromlist=["DouyinProvider"]).DouyinProvider.capture)
        self.assertIn("requested_rows", capture_source)
        self.assertIn("partial", capture_source)
        args = _parser().parse_args(["capture", "abc", "--output-dir", "out", "--douyin-rows", "2"])
        self.assertEqual(args.douyin_rows, 2)


if __name__ == "__main__":
    unittest.main()
