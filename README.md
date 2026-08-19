# social-capture

`social-capture` is an open-source, screenshot-only CLI for focused content
cards on Weibo, Zhihu, X, Xiaohongshu and Douyin. It connects to a local Chrome
session through CDP, locates the requested content card, and writes PNG images
plus a machine-readable manifest.

The project does not search, publish, comment, download original media, or
bundle platform SDKs. Search is intentionally a separate optional backend;
use `social-capture search-backends --json` to get installation and hand-off
hints. The CLI never silently clones or installs those projects. When a user
explicitly asks an AI to search, the AI may check for an existing backend and
install a missing upstream tool according to its instructions and license.

## Install

```powershell
cd E:\projectHome\social-capture
python -m pip install -e .
```

Start Chrome with remote debugging (for example, port `9221`) and log in to
the relevant platform. Playwright's browser binaries are not required when
connecting to an existing Chrome, although the Python package is required.

```powershell
social-capture providers
social-capture doctor --json
```

## Capture

`--output-dir` is required. There is no default output directory: the command
never writes screenshots into the repository or silently derives a path from
the current working directory.

```powershell
social-capture capture "https://weibo.com/<uid>/<bid>" `
  --output-dir "G:\screenshots\weibo"

social-capture capture "https://www.zhihu.com/question/123/answer/456" `
  --output-dir "G:\screenshots\zhihu"

social-capture capture "https://x.com/user/status/123" `
  --output-dir "G:\screenshots\x" --x-cut-stats

social-capture capture "https://www.xiaohongshu.com/explore/<note-id>?xsec_token=..." `
  --output-dir "G:\screenshots\xhs" --xhs-all-images --xhs-comments 3

social-capture capture "https://www.douyin.com/user/<sec_uid>" `
  --output-dir "G:\screenshots\douyin" --douyin-rows 2

social-capture capture --input .\urls.txt --output-dir "G:\screenshots\batch"
```

Use `--platform` when a value is ambiguous or supplied by a search backend:

```powershell
social-capture capture "<id>" --platform weibo --output-dir "G:\screenshots\one"
```

The output layout is stable:

```text
<user-output-dir>/
├── manifest.json
└── images/
    └── <platform>-<content-id>-01-of-N.png
```

Weibo and Zhihu long cards are split at a maximum 9:16 width:height ratio,
prefer content boundaries, and use a 64px overlap only for hard cuts. X keeps a
single tweet card by default. Xiaohongshu captures the visible note card and
can walk carousel frames with `--xhs-all-images` (the default comment limit is
8; use `--xhs-comments 0` to hide comments). Douyin captures the profile
header and up to the requested number of video rows (1–4, default 2); if fewer
rows are available, the manifest records the actual rows and a partial result.

Existing `manifest.json` or owned images cause a safe failure by default. Use
`--overwrite` only when replacing a chosen output directory; it removes only
image paths listed by a previous, valid social-capture manifest. Unlisted
same-looking files cause a safe failure, and unrelated files are left alone.

## Authentication and privacy

Authentication precedence is `--cookie-file`, then a platform environment
variable (`WEIBO_COOKIE`, `ZHIHU_COOKIE`, `X_COOKIE`, `XHS_COOKIE`, or
`DOUYIN_COOKIE`), then the already logged-in Chrome session. Cookie values,
Xiaohongshu `xsec_token`, and signature-like query values are redacted from
manifests and error messages. A temporary Chrome profile is started only when
the configured CDP endpoint is unavailable and an explicit Cookie was given;
it is closed and removed after the run. CAPTCHA and login challenges stop the
item and return a useful error instead of attempting to bypass them.

## Optional search backends

This repository remains screenshot-only. View the machine-readable routing
information:

```powershell
social-capture search-backends --json
```

The hints point to `dataabc/weibo-search` for Weibo,
`public-clis/twitter-cli` for X, `xpzouying/xiaohongshu-mcp` for Xiaohongshu,
and `NanmiCoder/MediaCrawler` for Douyin and Zhihu. MediaCrawler is declared
`Other` by its repository; read and follow its current license before use.
None of these projects is a dependency, and social-capture never installs or
clones them automatically. If the user explicitly requests a search, an AI may
install a missing backend after checking it is not present and reviewing its
license. Agent-Reach may be used as an optional routing layer for
X/Xiaohongshu when an AI workflow already has it installed.

## Exit codes

* `0`: every requested item succeeded.
* `2`: invalid input, authentication, browser, or page parsing failure.
* `3`: a batch completed with a mixture of successful and failed items.

## Development

```powershell
python -m pytest -q
python -m compileall -q src
ruff check .
git diff --check
```

See [docs/provider-api.md](docs/provider-api.md) for adding a provider and
[skill/SKILL.md](skill/SKILL.md) for AI/tool integration guidance.
