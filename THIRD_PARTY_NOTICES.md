# Third-party source notes

This project is an independent implementation. It does not import, vendor, or
runtime-depend on the historical implementations or external behavioral
references listed below. Their behavior and current
licenses may change; verify upstream before redistributing derived code.

| Area | Reference | License note |
|---|---|---|
| Weibo | Historical Weibo card/search implementation and [dataabc/weibo-search](https://github.com/dataabc/weibo-search) | The upstream repository's current license/terms must be checked before reuse. |
| Zhihu | Historical Zhihu answer/article capture implementation | The source license should be confirmed before copying implementation. |
| X | Historical X card capture implementation and [public-clis/twitter-cli](https://github.com/public-clis/twitter-cli) | `public-clis/twitter-cli` is referenced as Apache-2.0; verify the upstream LICENSE. |
| Xiaohongshu | Historical Xiaohongshu card capture implementation and [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) | The historical package declares MIT; keep upstream attribution/terms under review. The MCP reference is Apache-2.0 according to its repository. |
| Douyin | Historical Douyin profile capture implementation and [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | MediaCrawler declares `Other`; it is not bundled or a dependency. |

The new `social_capture` package contains only its own code under this
repository. Platform use remains subject to each platform's terms, login
requirements, and applicable law.
