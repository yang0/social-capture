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
  // Trigger the profile page's lazy loaders, then return to the top so the
  // first rows and the account header are both present in the DOM.
  window.scrollTo(0, Math.max(0, Math.floor(window.innerHeight * 0.9)));
  await sleep(450);
  window.scrollTo(0, 0);
  await sleep(300);
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const textOf = el => (el?.innerText || el?.textContent || '').trim().replace(/\s+/g, ' ');

  // Do not use a generic header or a post card as a profile fallback. The
  // account page currently exposes this stable semantic marker; the field
  // checks below make an empty shell or navigation header fail explicitly.
  const profile = Array.from(document.querySelectorAll('[data-e2e="user-info"]')).find(visible);
  const profileText = textOf(profile);
  const nicknameNode = profile?.querySelector('h1');
  const nickname = textOf(nicknameNode).replace(/\s+/g, ' ').trim();
  const accountMatch = profileText.match(/抖音号\s*[：:]\s*([^\s]+)/);
  const accountId = accountMatch ? accountMatch[1].trim() : '';
  const identityNode = profile && Array.from(profile.querySelectorAll('p,span,div')).find(node => {
    const value = textOf(node);
    return /抖音号\s*[：:]/.test(value) && value.length < 180;
  });
  const identityText = textOf(identityNode || profile?.querySelector('p'));
  const stats = profile ? Array.from(profile.querySelectorAll('[data-e2e^="user-info-"]'))
    .filter(visible)
    .map(node => textOf(node))
    .filter(Boolean) : [];
  const statsFound = stats.length >= 2;
  const avatar = Array.from(document.querySelectorAll('[data-e2e="live-avatar"] img')).find(image => {
    const rect = image.getBoundingClientRect();
    return visible(image) && rect.width >= 48 && rect.height >= 48;
  });
  const avatarUrl = avatar?.currentSrc || avatar?.src || '';
  const bioNode = profile && Array.from(profile.querySelectorAll('div,span')).find(node => {
    const value = textOf(node);
    return value && /视频同|直播在|普通人|简介/.test(value) && value.length < 300;
  });
  const bio = textOf(bioNode);
  const profileFields = [];
  if (nickname) profileFields.push('nickname');
  if (accountId) profileFields.push('account_id');
  if (avatarUrl) profileFields.push('avatar');
  if (statsFound) profileFields.push('stats');
  if (identityText) profileFields.push('identity');
  if (bio) profileFields.push('bio');
  if (!profile || !nickname || !accountId) {
    return {
      ok: false,
      code: 'profile-not-found',
      profile_found: false,
      profile_fields: profileFields,
      nickname: nickname || null,
      stats_found: statsFound,
    };
  }

  const cardCandidates = [];
  for (const selector of ['[data-e2e="user-post-item"]', 'a[href*="/video/"]', 'a[href*="/note/"]']) {
    for (const node of document.querySelectorAll(selector)) {
      const rect = node.getBoundingClientRect();
      const hasMedia = !!node.querySelector('img, video, [class*="cover"]');
      if (visible(node) && rect.width >= 100 && rect.height >= 100 && hasMedia) cardCandidates.push(node);
    }
  }
  const seenCards = new Set();
  const cards = [];
  for (const node of cardCandidates) {
    const rect = node.getBoundingClientRect();
    const href = node.getAttribute('href') || '';
    const key = href || [rect.left, rect.top, rect.width, rect.height]
      .map(value => Math.round(value * 10) / 10).join(':');
    if (seenCards.has(key)) continue;
    seenCards.add(key);
    cards.push(node);
  }
  const heights = cards.map(node => node.getBoundingClientRect().height).sort((a, b) => a - b);
  const medianHeight = heights.length ? heights[Math.floor(heights.length / 2)] : 0;
  const tolerance = Math.max(8, Math.min(48, medianHeight * 0.22));
  const grouped = [];
  for (const node of cards) {
    const top = node.getBoundingClientRect().top;
    let row = grouped.find(candidate => Math.abs(candidate.top - top) <= tolerance);
    if (!row) { row = {top, nodes: []}; grouped.push(row); }
    row.nodes.push(node);
  }
  grouped.sort((a, b) => a.top - b.top);
  for (const row of grouped) row.nodes.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
  const chosenRows = grouped.slice(0, Math.max(1, Math.min(4, rows)));
  const chosen = chosenRows.flatMap(row => row.nodes);
  const columns = chosenRows.length ? Math.max(1, chosenRows[0].nodes.length) : 0;
  if (!chosen.length || !columns) {
    return {
      ok: false,
      code: 'posts-not-found',
      profile_found: true,
      profile_fields: profileFields,
      nickname,
      stats_found: statsFound,
    };
  }

  // Remove a previous probe if a caller retries in the same page.
  document.querySelectorAll('[data-social-capture-douyin="true"]').forEach(node => node.remove());
  const host = document.createElement('div');
  host.setAttribute('data-social-capture-douyin', 'true');
  Object.assign(host.style, {
    background: '#fff', color: '#161823', width: 'min(1320px, 100vw)', padding: '24px',
    boxSizing: 'border-box', fontFamily: 'Arial, "Microsoft YaHei", sans-serif', position: 'absolute',
    left: '0', top: (document.documentElement.scrollHeight + 80) + 'px', zIndex: '2147483647'
  });
  const header = document.createElement('section');
  header.setAttribute('data-social-capture-douyin-profile', 'true');
  Object.assign(header.style, {
    display: 'flex', alignItems: 'center', gap: '20px', padding: '20px 24px',
    border: '1px solid #edf0f2', borderRadius: '12px', boxSizing: 'border-box',
    background: '#fff', minHeight: '148px'
  });
  if (avatarUrl) {
    const avatarImage = document.createElement('img');
    avatarImage.src = avatarUrl;
    avatarImage.alt = nickname + '头像';
    Object.assign(avatarImage.style, {width: '96px', height: '96px', borderRadius: '50%', objectFit: 'cover', flex: '0 0 96px', display: 'block'});
    header.appendChild(avatarImage);
  }
  const profileBody = document.createElement('div');
  Object.assign(profileBody.style, {minWidth: '0', flex: '1', display: 'flex', flexDirection: 'column', gap: '8px'});
  const title = document.createElement('div');
  title.innerText = nickname;
  Object.assign(title.style, {fontSize: '28px', fontWeight: '700', lineHeight: '1.2', color: '#161823'});
  profileBody.appendChild(title);
  const statsLine = document.createElement('div');
  Object.assign(statsLine.style, {display: 'flex', gap: '22px', fontSize: '16px', lineHeight: '1.3', color: '#4f5359'});
  for (const value of stats) {
    const item = document.createElement('span');
    item.innerText = value;
    statsLine.appendChild(item);
  }
  if (stats.length) profileBody.appendChild(statsLine);
  const identity = document.createElement('div');
  identity.innerText = identityText || ('抖音号：' + accountId);
  Object.assign(identity.style, {fontSize: '14px', color: '#74777c', lineHeight: '1.4'});
  profileBody.appendChild(identity);
  if (bio) {
    const description = document.createElement('div');
    description.innerText = bio;
    Object.assign(description.style, {fontSize: '14px', color: '#4f5359', lineHeight: '1.4', maxWidth: '720px'});
    profileBody.appendChild(description);
  }
  header.appendChild(profileBody);
  host.appendChild(header);

  const grid = document.createElement('div');
  Object.assign(grid.style, {
    display: 'grid', gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: '14px', marginTop: '18px'
  });
  for (const card of chosen) {
    const clone = card.cloneNode(true);
    Object.assign(clone.style, {display: 'block', width: '100%', minWidth: '0', height: 'auto', overflow: 'hidden', borderRadius: '10px', background: '#111'});
    const sourceImages = Array.from(card.querySelectorAll('img'));
    for (const [index, image] of Array.from(clone.querySelectorAll('img')).entries()) {
      const source = sourceImages[index];
      const url = source?.currentSrc || source?.src || image.src;
      if (url) { image.src = url; image.removeAttribute('srcset'); }
      Object.assign(image.style, {display: 'block', width: '100%', height: 'auto', objectFit: 'cover'});
    }
    grid.appendChild(clone);
  }
  host.appendChild(grid);
  document.body.appendChild(host);
  host.scrollIntoView({block: 'start'});
  await Promise.all(Array.from(host.querySelectorAll('img')).map(image => image.complete ? Promise.resolve() : new Promise(resolve => {
    image.addEventListener('load', resolve, {once: true});
    image.addEventListener('error', resolve, {once: true});
    setTimeout(resolve, 3000);
  })));
  const rect = host.getBoundingClientRect();
  return {
    ok: true,
    selector: '[data-social-capture-douyin="true"]',
    profile_found: true,
    profile_fields: profileFields,
    nickname,
    account_id: accountId,
    stats_found: statsFound,
    rows: chosenRows.length,
    cards: chosen.length,
    columns,
    requested_rows: rows,
    partial: chosenRows.length < rows,
    clip: {x: rect.left, y: rect.top, width: rect.width, height: rect.height}
  };
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
            code = str(info.get("code") or "profile-not-found") if isinstance(info, dict) else "profile-not-found"
            if code == "posts-not-found":
                raise TargetNotFoundError("已定位抖音账号资料，但无法定位视频作品卡片；已拒绝整页截图", code=code)
            raise TargetNotFoundError("无法验证抖音账号资料（需要昵称和抖音号）；已拒绝整页截图", code="profile-not-found")
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
                "profile_found": bool(info.get("profile_found")),
                "profile_fields": list(info.get("profile_fields") or []),
                "nickname": info.get("nickname"),
                "stats_found": bool(info.get("stats_found")),
                "account_id": info.get("account_id"),
                "columns": int(info.get("columns") or 0),
                "clip": info.get("clip"),
            },
        )
