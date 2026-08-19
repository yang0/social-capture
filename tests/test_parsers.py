from __future__ import annotations

import unittest

from social_capture.errors import InputError
from social_capture.providers.douyin import parse_reference as parse_douyin
from social_capture.providers.weibo import parse_reference as parse_weibo
from social_capture.providers.x import parse_reference as parse_x
from social_capture.providers.xiaohongshu import parse_reference as parse_xhs
from social_capture.providers.zhihu import parse_reference as parse_zhihu
from social_capture.registry import detect_provider, list_providers


class ParserTests(unittest.TestCase):
    def test_all_builtin_platforms_are_registered_without_search_capability(self):
        self.assertEqual({p.name for p in list_providers()}, {"weibo", "zhihu", "x", "xiaohongshu", "douyin"})
        for provider in list_providers():
            self.assertNotIn("search", provider.capabilities)

    def test_weibo_and_auto_numeric(self):
        reference = parse_weibo("123456")
        self.assertEqual(reference.content_id, "123456")
        self.assertEqual(detect_provider("123456").name, "weibo")

    def test_explicit_x_numeric_status_id(self):
        reference = parse_x("2089900346328158261")
        self.assertEqual(reference.content_id, "2089900346328158261")
        self.assertIn("/status/2089900346328158261", reference.url)

    def test_x_and_zhihu(self):
        self.assertEqual(parse_x("https://x.com/u/status/123?utm_source=x").content_id, "123")
        answer = parse_zhihu("https://www.zhihu.com/question/10/answer/20")
        self.assertEqual((answer.content_type, answer.content_id), ("answer", "20"))
        article = parse_zhihu("https://zhuanlan.zhihu.com/p/42")
        self.assertEqual((article.content_type, article.content_id), ("article", "42"))

    def test_xhs_signed_and_short_urls(self):
        signed = parse_xhs("https://www.xiaohongshu.com/explore/abc?xsec_token=secret")
        self.assertEqual(signed.content_id, "abc")
        short = parse_xhs("https://xhslink.com/a/short-code")
        self.assertEqual(short.content_id, "short-code")
        with self.assertRaises(InputError):
            parse_xhs("https://www.xiaohongshu.com/explore/abc")

    def test_douyin_profile_only(self):
        reference = parse_douyin("https://www.douyin.com/user/sec_uid")
        self.assertEqual(reference.content_type, "profile")
        with self.assertRaises(InputError):
            parse_douyin("https://www.douyin.com/search/cats?type=user")


if __name__ == "__main__":
    unittest.main()
