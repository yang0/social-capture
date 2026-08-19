"""Read-only hints for optional external search tools.

This project only captures screenshots. The hints let an AI or user choose an
independent search project without making it a runtime dependency or silently
installing third-party code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SearchBackendHint:
    platform: str
    name: str
    built_in: bool
    repo_url: str
    install_hint: str
    command_hint: str
    license_note: str
    package_markers: tuple[str, ...] = ()

    def as_dict(self, *, installed: bool = False) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "name": self.name,
            "built_in": self.built_in,
            "installed": installed,
            "repo_url": self.repo_url,
            "install_hint": self.install_hint,
            "command_hint": self.command_hint,
            "license_note": self.license_note,
        }


SEARCH_BACKENDS: dict[str, SearchBackendHint] = {
    "weibo": SearchBackendHint(
        "weibo",
        "weibo-search",
        False,
        "https://github.com/dataabc/weibo-search",
        "git clone https://github.com/dataabc/weibo-search.git",
        "按该项目 README 配置后执行其搜索命令，再把详情 URL 交给 social-capture capture",
        "外部项目许可证和平台条款以仓库当前版本为准；不作为本项目依赖。",
        ("weibo-search", "dataabc"),
    ),
    "x": SearchBackendHint(
        "x",
        "twitter-cli",
        False,
        "https://github.com/public-clis/twitter-cli",
        "git clone https://github.com/public-clis/twitter-cli.git",
        "按该项目 README 配置后执行搜索，再把推文 URL 交给 social-capture capture",
        "Apache-2.0；外部项目不随本项目捆绑。",
        ("twitter-cli", "public-clis"),
    ),
    "xiaohongshu": SearchBackendHint(
        "xiaohongshu",
        "xiaohongshu-mcp",
        False,
        "https://github.com/xpzouying/xiaohongshu-mcp",
        "git clone https://github.com/xpzouying/xiaohongshu-mcp.git",
        "按该项目 README 启动搜索能力，再把笔记 URL 交给 social-capture capture",
        "Apache-2.0；外部项目不作为本项目依赖。",
        ("xiaohongshu-mcp", "xpzouying"),
    ),
    "douyin": SearchBackendHint(
        "douyin",
        "MediaCrawler",
        False,
        "https://github.com/NanmiCoder/MediaCrawler",
        "git clone https://github.com/NanmiCoder/MediaCrawler.git",
        "按该项目 README 配置 dy 搜索，再把账号/作品 URL 交给 social-capture capture",
        "仓库声明为 Other；使用前必须阅读并遵守其当前许可证和平台条款，本项目不捆绑。",
        ("MediaCrawler", "NanmiCoder"),
    ),
    "zhihu": SearchBackendHint(
        "zhihu",
        "MediaCrawler",
        False,
        "https://github.com/NanmiCoder/MediaCrawler",
        "git clone https://github.com/NanmiCoder/MediaCrawler.git",
        "按该项目 README 配置 zhihu 搜索，再把回答/文章 URL 交给 social-capture capture",
        "仓库声明为 Other；使用前必须阅读并遵守其当前许可证和平台条款，本项目不捆绑。",
        ("MediaCrawler", "NanmiCoder"),
    ),
}


def search_hint(platform: str) -> SearchBackendHint | None:
    return SEARCH_BACKENDS.get(platform)
