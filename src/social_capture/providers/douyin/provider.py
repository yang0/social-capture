from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from ...errors import InputError, TargetNotFoundError
from ...models import CaptureArtifact, CapturedImage, CaptureOptions, CaptureReference
from ..base import CaptureProvider, clean_id
from ..common import check_page_state, open_site_page, page_text

_SEC_UID_RE = re.compile(r"^[A-Za-z0-9_-]{2,}$")


def parse_reference(value: str) -> CaptureReference:
    raw = str(value or "").strip()
    if not raw:
        raise InputError("抖音账号主页 URL/sec_uid 不能为空")
    if _SEC_UID_RE.fullmatch(raw):
        return CaptureReference("douyin", clean_id(raw), f"https://www.douyin.com/user/{raw}", raw, "profile")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"douyin.com", "www.douyin.com", "iesdouyin.com"}:
        raise InputError("抖音只接受 douyin.com/user/<sec_uid> 账号主页 URL 或 sec_uid")
    parts = [part for part in parsed.path.split("/") if part]
    if "user" not in parts or parts.index("user") + 1 >= len(parts):
        raise InputError("抖音 URL 必须是账号主页 /user/<sec_uid>")
    sec_uid = parts[parts.index("user") + 1]
    if not _SEC_UID_RE.fullmatch(sec_uid):
        raise InputError("抖音 sec_uid 格式无效")
    canonical = urlunparse((parsed.scheme, parsed.netloc, f"/user/{sec_uid}", "", "", ""))
    return CaptureReference("douyin", clean_id(sec_uid), canonical, raw, "profile", {"sec_uid": sec_uid})


_PROFILE_SCRIPT = r"""
async ({rows}) => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  window.scrollTo(0, Math.max(0, Math.floor(window.innerHeight * 0.9)));
  await sleep(350);
  window.scrollTo(0, 0);
  await sleep(250);
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 100 && r.height > 25; };
  const profileSelectors = ['[data-e2e="user-info"]', '[data-e2e="user-profile"]', '[data-e2e="user-info-container"]', '[class*="author-card"]', 'header'];
  let profile = null;
  for (const selector of profileSelectors) { profile = Array.from(document.querySelectorAll(selector)).find(visible); if (profile) break; }
  const cardSelectors = ['[data-e2e="user-post-item"]', '[data-e2e="user-post-list"] a[href*="/video/"]', '[data-e2e="user-post-list"] a[href*="/note/"]'];
  const cardCandidates = [];
  for (const selector of cardSelectors) for (const node of document.querySelectorAll(selector)) {
    if (visible(node)) cardCandidates.push(node);
  }
  const seenRects = new Set();
  const cards = [];
  for (const node of cardCandidates) {
    const rect = node.getBoundingClientRect();
    const key = [rect.left, rect.top, rect.width, rect.height]
      .map(value => Math.round(value * 10) / 10).join(':');
    if (seenRects.has(key)) continue;
    seenRects.add(key);
    cards.push(node);
  }
  const selected = cards.slice(0, 12);
  const heights = selected.map(node => node.getBoundingClientRect().height).sort((a,b) => a-b);
  const medianHeight = heights.length ? heights[Math.floor(heights.length / 2)] : 0;
  const tolerance = Math.max(8, Math.min(36, medianHeight * 0.2));
  const grouped = [];
  for (const node of selected) {
    const top = node.getBoundingClientRect().top;
    let row = grouped.find(candidate => Math.abs(candidate.top - top) <= tolerance);
    if (!row) { row = {top, nodes: []}; grouped.push(row); }
    row.nodes.push(node);
  }
  grouped.sort((a,b) => a.top - b.top);
  const chosenRows = grouped.slice(0, Math.max(1, Math.min(4, rows)));
  const chosen = chosenRows.flatMap(row => row.nodes);
  const chosenTops = chosenRows.map(row => row.top);
  if (!profile && !chosen.length) return {ok: false, code: 'profile-not-found'};
  const host = document.createElement('div');
  host.setAttribute('data-social-capture-douyin', 'true');
  Object.assign(host.style, {background:'#fff', color:'#161823', width:'min(1320px, 100vw)', padding:'24px', boxSizing:'border-box', fontFamily:'Arial, sans-serif', position:'absolute', left:'0', top:(document.documentElement.scrollHeight + 80)+'px', zIndex:'2147483647'});
  if (profile) {
    const clone = profile.cloneNode(true);
    clone.style.display = 'block'; clone.style.width = '100%';
    host.appendChild(clone);
  }
  const grid = document.createElement('div');
  Object.assign(grid.style, {display:'grid', gridTemplateColumns:'repeat(3, minmax(0, 1fr))', gap:'14px', marginTop:'18px'});
  for (const card of chosen) {
    const clone = card.cloneNode(true); clone.style.display='block'; clone.style.width='100%';
    for (const [i, image] of Array.from(clone.querySelectorAll('img')).entries()) {
      const source = card.querySelectorAll('img')[i];
      const url = source?.currentSrc || source?.src || image.src;
      if (url) { image.src = url; image.removeAttribute('srcset'); }
    }
    grid.appendChild(clone);
  }
  host.appendChild(grid); document.body.appendChild(host); host.scrollIntoView({block:'start'});
  await Promise.all(Array.from(host.querySelectorAll('img')).map(image => image.complete ? Promise.resolve() : new Promise(resolve => { image.addEventListener('load', resolve, {once:true}); image.addEventListener('error', resolve, {once:true}); setTimeout(resolve, 3000); })));
  const rect = host.getBoundingClientRect();
  return {ok:true, selector:'[data-social-capture-douyin="true"]', rows:chosenTops.length, cards:chosen.length, requested_rows:rows, clip:{x:rect.left, y:rect.top, width:rect.width, height:rect.height}};
}
"""


class DouyinProvider(CaptureProvider):
    name = "douyin"
    hosts = ("douyin.com", "www.douyin.com", "iesdouyin.com")
    capabilities = ("profile", "profile-grid", "video-cover")

    def can_handle(self, value: str) -> bool:
        raw = str(value or "").strip()
        return bool(_SEC_UID_RE.fullmatch(raw)) or (urlparse(raw).hostname or "").lower() in self.hosts

    def parse_reference(self, value: str) -> CaptureReference:
        return parse_reference(value)

    async def capture(self, browser: object, reference: CaptureReference, options: CaptureOptions) -> CaptureArtifact:
        rows = max(1, min(4, int(options.douyin_rows)))
        page = await open_site_page(browser, reference.url, options.wait_seconds)
        text = await page_text(page)
        check_page_state(text, platform="抖音")
        info = await page.evaluate(_PROFILE_SCRIPT, {"rows": rows})
        if not isinstance(info, dict) or not info.get("ok"):
            raise TargetNotFoundError("无法定位抖音账号资料和视频卡片；已拒绝整页截图", code="profile-not-found")
        locator = page.locator(str(info["selector"]))
        if await locator.count() == 0:
            raise TargetNotFoundError("抖音账号截图区域已消失", code="profile-not-found")
        screenshot = await locator.first.screenshot(type="png", animations="disabled", timeout=30_000)
        return CaptureArtifact(
            (CapturedImage(screenshot, label="profile-grid"),),
            "profile-region",
            split_long=False,
            metadata={
                "requested_rows": rows,
                "captured_rows": int(info.get("rows") or 0),
                "post_cards_considered": int(info.get("cards") or 0),
                "partial": int(info.get("rows") or 0) < rows,
                "clip": info.get("clip"),
            },
        )
