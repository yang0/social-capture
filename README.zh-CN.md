# social-capture（多平台内容截图）

`social-capture` 是一个只负责截图的开源 CLI，内置微博、知乎、X、小红书
和抖音五个平台。它通过 Chrome CDP 连接本地浏览器，定位指定内容卡片，
输出 PNG 和机器可读的 `manifest.json`。

本项目不包含搜索、发布、评论、原始媒体下载或平台 SDK。搜索属于独立的
可选后端；用户需要搜索时先执行 `social-capture search-backends --json`，
再由 AI 根据指引安装/调用外部项目。工具不会自动 clone、安装或执行任何
第三方搜索项目。

## 安装与检查

```powershell
cd E:\projectHome\social-capture
python -m pip install -e .
social-capture providers
social-capture doctor --json
```

启动带远程调试端口（例如 `9221`）的 Chrome，并在需要的平台登录。默认
复用现有 Chrome，不需要下载 Playwright 自带浏览器。

## 截图

`--output-dir` 是必填项。项目没有默认输出目录，不会把截图写入项目目录，
也不会静默使用当前工作目录。

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

可以使用 `--platform weibo|zhihu|x|xiaohongshu|douyin` 显式指定平台。

微博和知乎长内容按最大 9:16 宽高比分片，优先在正文/图片/转发边界切分，
硬切时保留 64px 重叠。X 默认保持一张真实推文卡片；小红书可遍历可见轮播
帧，默认保留前 8 条评论（`--xhs-comments 0` 隐藏）；抖音默认截图账号资料和按请求的两排视频卡片（支持 1–4 排），作品不足时 manifest 会记录实际排数并标记 partial。

输出结构：

```text
<用户指定目录>/
├── manifest.json
└── images/
    └── <platform>-<content-id>-01-of-N.png
```

默认拒绝已有 `manifest.json` 或本工具图片的目录。显式使用 `--overwrite`
时只清理旧 social-capture manifest 明确列出的图片；未列出的同名图片会安全失败，
其他用户文件不会被递归删除。

## 认证与隐私

认证优先级为：`--cookie-file`、平台环境变量（`WEIBO_COOKIE`、`ZHIHU_COOKIE`、
`X_COOKIE`、`XHS_COOKIE`、`DOUYIN_COOKIE`）、已登录 Chrome。Cookie、
`xsec_token` 和签名类 URL 参数不会写入 manifest 或错误信息。CDP 不可用且
明确提供 Cookie 时，才会启动临时 Chrome；任务结束后关闭并清理。验证码和
登录挑战会停止该条任务，不自动绕过。

## 可选搜索后端

```powershell
social-capture search-backends --json
```

推荐映射为：微博 `dataabc/weibo-search`，X `public-clis/twitter-cli`，小红书
`xpzouying/xiaohongshu-mcp`，抖音/知乎 `NanmiCoder/MediaCrawler`。其中
MediaCrawler 仓库声明为 `Other`，使用前必须阅读并遵守其当前许可证；这些
项目都不是本项目依赖，也不会被工具静默安装。用户明确请求搜索时，AI 可在检查
现有安装并阅读许可证后安装缺失后端。已有 Agent-Reach 的 AI 工作流可将其作为
X/小红书的可选路由层。

## 返回码

`0` 全部成功；`2` 参数/认证/浏览器/页面解析失败；`3` 批量任务部分成功。

## 开发

```powershell
python -m pytest -q
python -m compileall -q src
ruff check .
git diff --check
```

参见 [docs/provider-api.md](docs/provider-api.md) 和 [skill/SKILL.md](skill/SKILL.md)。
