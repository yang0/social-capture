# Authentication

The CLI accepts a Cookie header file or environment variable. Precedence is:

1. `--cookie-file`;
2. the platform variable (`WEIBO_COOKIE`, `ZHIHU_COOKIE`, `X_COOKIE`,
   `XHS_COOKIE`, `DOUYIN_COOKIE`);
3. the existing logged-in Chrome connected through CDP.

Cookie files may contain a plain Cookie header, a JSON name/value map, or a
Playwright/Chrome cookie list. Values are used only to add browser context
cookies. They are not written to `manifest.json`, stdout, or error text.

If the CDP endpoint is unavailable, `BrowserSession` launches a temporary
Chrome only if a Cookie was explicitly supplied. The profile is created in the
system temporary directory and removed after the capture. CAPTCHA, login, and
security challenge pages stop the item; there is no bypass logic.
