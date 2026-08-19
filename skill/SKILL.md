---
name: social-capture
description: Capture a focused screenshot card from Weibo, Zhihu, X, Xiaohongshu or Douyin through a logged-in local Chrome CDP session.
version: 0.1.0
---

# social-capture skill

Use this skill when an AI needs an evidence image of a specific social post,
answer, note, tweet, or Douyin profile. The project is screenshot-only: it
does not search, publish, reply, download original media, or bypass CAPTCHAs.

## Workflow

1. Ask for or identify one or more canonical content URLs. Do not invent a URL.
2. Ask the user for an explicit output directory. Never use the repository or a
   default `downloads` folder.
3. Check capabilities with `social-capture providers` and environment health
   with `social-capture doctor --json`.
4. If search is explicitly requested by the user, run the read-only
   `social-capture search-backends --json` command, check whether the hinted
   backend already exists, and install only a missing backend using its upstream
   instructions. The AI may perform that explicitly requested installation and
   then run the backend; social-capture itself never silently installs, clones,
   or runs third-party projects. Read and follow each backend's license,
   authentication, and platform terms first.
5. Capture with `social-capture capture URL --output-dir DIR`. Use
   `--platform` when auto-detection is ambiguous.
6. Read `manifest.json`, verify `status`, dimensions, and SHA-256 before using
   an image in content. Report blocked, deleted, or partial items honestly.

## Examples

```powershell
social-capture capture "https://x.com/user/status/123" --output-dir "G:\captures\x"
social-capture capture "https://www.zhihu.com/question/1/answer/2" --output-dir "G:\captures\zhihu"
social-capture capture "https://www.xiaohongshu.com/explore/ID?xsec_token=..." --output-dir "G:\captures\xhs" --xhs-all-images
social-capture capture "https://www.douyin.com/user/SEC_UID" --output-dir "G:\captures\douyin" --douyin-rows 2
```

For Xiaohongshu, signed `xiaohongshu.com` URLs must include `xsec_token`;
short `xhslink.com` URLs are accepted and resolved by Chrome. Use
`--xhs-comments 0` to hide comments; the default is eight comments.

## Search routing hints

Search is an optional external action and must be separately authorized by the
user. The current read-only hints are:

- Weibo: `https://github.com/dataabc/weibo-search`
- X: `https://github.com/public-clis/twitter-cli` (Apache-2.0)
- Xiaohongshu: `https://github.com/xpzouying/xiaohongshu-mcp` (Apache-2.0)
- Douyin/Zhihu: `https://github.com/NanmiCoder/MediaCrawler` (`Other`; read its license first)

Agent-Reach can be an optional routing layer for X/Xiaohongshu in workflows
that already have it installed. None of these projects is a social-capture
dependency.

## Safety

Do not expose Cookie headers, `xsec_token`, signed URLs, or temporary profile
paths in generated prose. A screenshot must be a real content-card crop; if a
Provider cannot uniquely locate the target, preserve the error instead of
capturing a feed or full page.
