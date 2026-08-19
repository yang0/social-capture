from __future__ import annotations

import re
from urllib.parse import urlparse

from ...errors import InputError, TargetNotFoundError
from ...models import CaptureArtifact, CapturedImage, CaptureOptions, CaptureReference
from ..base import CaptureProvider, clean_id
from ..common import (
    capture_locator_banded,
    check_page_state,
    open_site_page,
    page_text,
)


def parse_reference(value: str) -> CaptureReference:
    raw = str(value or "").strip()
    if not raw:
        raise InputError("知乎回答/文章 URL 不能为空")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"zhihu.com", "www.zhihu.com", "zhuanlan.zhihu.com"}:
        raise InputError("知乎只接受 zhihu.com 或 zhuanlan.zhihu.com URL")
    parts = [part for part in parsed.path.split("/") if part]
    content_type = "article"
    content_id = ""
    if "answer" in parts:
        index = parts.index("answer")
        content_id = parts[index + 1] if index + 1 < len(parts) else ""
        content_type = "answer"
    elif parts and parts[0] in {"p", "question"}:
        content_id = parts[-1]
        content_type = "article" if parts[0] == "p" or host.startswith("zhuanlan.") else "question"
    elif parts:
        content_id = parts[-1]
    if not content_id or not re.fullmatch(r"[A-Za-z0-9_-]+", content_id):
        raise InputError("无法从知乎 URL 识别回答/文章 ID")
    return CaptureReference("zhihu", clean_id(content_id), raw, raw, content_type=content_type)


class ZhihuProvider(CaptureProvider):
    name = "zhihu"
    hosts = ("zhihu.com", "www.zhihu.com", "zhuanlan.zhihu.com")
    capabilities = ("answer", "article", "multi-image", "long-content")

    def can_handle(self, value: str) -> bool:
        host = (urlparse(str(value or "")).hostname or "").lower()
        return host in self.hosts

    def parse_reference(self, value: str) -> CaptureReference:
        return parse_reference(value)

    async def capture(self, browser: object, reference: CaptureReference, options: CaptureOptions) -> CaptureArtifact:
        page = await open_site_page(browser, reference.url, options.wait_seconds)
        text = await page_text(page)
        check_page_state(text, platform="知乎")
        prepared = await page.evaluate(
            _PREPARE_SCRIPT,
            {"targetId": reference.content_id, "targetType": reference.content_type},
        )
        if not isinstance(prepared, dict) or not prepared.get("ok"):
            reason = (prepared or {}).get("reason") if isinstance(prepared, dict) else "unknown"
            raise TargetNotFoundError(f"无法定位精确知乎主体（{reason or 'target-not-found'}）")
        selector = str(prepared["selector"])
        locator = page.locator(selector).first
        if await locator.count() == 0:
            raise TargetNotFoundError("知乎截图主体已消失；已拒绝整页截图")
        image, info = await capture_locator_banded(
            page,
            locator,
            boundary_selectors=("h1", "h2", "h3", "p", "figure", "img", "blockquote", "pre", ".RichContent-inner"),
        )
        title = str(prepared.get("title") or "").strip()[:300]
        return CaptureArtifact(
            (CapturedImage(image, tuple(info.get("boundaries") or ())),),
            reference.content_type,
            split_long=True,
            metadata={
                "selector": selector,
                "title_excerpt": title,
                "clip": info,
                "image_count": int(prepared.get("imageCount") or 0),
                "removed_image_count": int(prepared.get("removedImageCount") or 0),
            },
        )


_PREPARE_SCRIPT = r"""
async ({targetId, targetType}) => {
  const text = el => (el && (el.innerText || el.textContent) || '').trim();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const hide = root => root.querySelectorAll('nav,header[role="banner"],.Comments,.Comments-container,[class*="comment"],.Answer-footer,.ContentItem-actions,.RichContent-actions,.Post-SideActions,.Post-CommentButton').forEach(node => { node.style.display='none'; });
  const expand = root => root.querySelectorAll('button,a,[role="button"],span').forEach(node => { const value=text(node); if (/^(全文|阅读全文|展开全文|显示全部|展开)$/.test(value) && node.offsetParent !== null) { try { node.click(); } catch (_) {} } });
  const body = text(document.body);
  if (/请登录后查看|登录后查看|请先登录|安全验证|滑动验证|验证码|验证后继续/.test(body) || location.pathname.includes('/signin')) return {ok:false, reason:'auth-or-challenge'};
  if (/该内容已被删除|内容不存在|文章不存在|问题不存在/.test(body)) return {ok:false, reason:'unavailable'};
  const answerCandidates = Array.from(document.querySelectorAll('.ContentItem.AnswerItem,.AnswerItem'));
  const exactAnswer = node => {
    if (Array.from(node.querySelectorAll('a[href]')).some(a => (a.getAttribute('href')||'').includes('/answer/' + targetId))) return true;
    return ['data-zop','data-za-extra-module','data-id','data-item-id','id','name','data-zop-answer-id'].some(name => (node.getAttribute(name)||'').includes(targetId));
  };
  let source = null, title = null, mode = targetType;
  if (targetType === 'answer') {
    source = answerCandidates.find(exactAnswer) || null;
    title = document.querySelector('.QuestionHeader-title');
    if (!source || !title) return {ok:false, reason:!source ? 'answer-not-found' : 'question-title-not-found'};
    expand(source); hide(source); hide(title);
  } else if (targetType === 'article') {
    source = document.querySelector('article.Post-Main,.Post-Main');
    if (!source) return {ok:false, reason:'post-main-not-found'};
    expand(source); hide(source); mode='article-post-main';
  } else return {ok:false, reason:'unsupported-type'};
  await sleep(650);
  const sourceImages = root => Array.from(root.querySelectorAll('img'));
  const sourceOf = image => {
    const values = [image.currentSrc, image.getAttribute('src'), image.getAttribute('data-original'), image.getAttribute('data-actualsrc'), image.getAttribute('data-src'), image.getAttribute('data-lazy-src'), image.getAttribute('data-original-src'), image.getAttribute('data-image-url'), image.getAttribute('data-url')];
    for (const raw of values) { const value=String(raw||'').trim(); if (!value || /^data:|about:blank|none$/i.test(value) || /placeholder|spacer|transparent|blank|loading|pixel|1x1/i.test(value)) continue; try { const url=new URL(value,location.href); if (['http:','https:'].includes(url.protocol)) return url.href; } catch (_) {} }
    for (const raw of [image.getAttribute('srcset'),image.getAttribute('data-srcset')]) for (const part of String(raw||'').split(',')) { const value=part.trim().split(/\s+/)[0]; if (value) { try { const url=new URL(value,location.href); if (['http:','https:'].includes(url.protocol)) return url.href; } catch (_) {} } }
    return '';
  };
  const pixels = image => image.complete && image.naturalWidth > 2 && image.naturalHeight > 2;
  const load = (image,url) => new Promise(resolve => { let done=false; const finish=ok=>{if(done)return;done=true;clearTimeout(timer);image.removeEventListener('load',onload);image.removeEventListener('error',onerror);resolve(ok);}; const onload=()=>finish(pixels(image)); const onerror=()=>finish(false); const timer=setTimeout(()=>finish(pixels(image)),3500); image.loading='eager'; image.removeAttribute('srcset'); image.removeAttribute('data-srcset'); image.addEventListener('load',onload,{once:true}); image.addEventListener('error',onerror,{once:true}); image.src=url; if (pixels(image)) finish(true); });
  const materialize = async root => { const states=[]; for (const image of sourceImages(root)) { try { image.scrollIntoView({block:'center',inline:'nearest'}); } catch (_) {} await sleep(45); const url=sourceOf(image); const ok=Boolean(url) && (pixels(image) && sourceOf(image)===url || await load(image,url)); states.push({image,url,ok}); } return states; };
  const answerStates = await materialize(source); const titleStates = title ? await materialize(title) : [];
  const wrapper = document.createElement('div'); wrapper.setAttribute('data-social-capture-zhihu','true');
  Object.assign(wrapper.style,{position:'absolute',left:'0',top:(document.documentElement.scrollHeight+80)+'px',width:'min(960px, calc(100vw - 48px))',boxSizing:'border-box',background:'#fff',color:'#18191c',padding:'0 0 24px',zIndex:'2147483647',overflow:'visible'});
  const transfer = (original,clone,states) => { const clones=Array.from(clone.querySelectorAll('img')); clones.forEach((image,index)=>{ const state=states[index]; if (!state || !state.ok || !state.url) { let node=image; while(node && node!==clone && node.children.length<=1 && !text(node)){ const parent=node.parentElement; node.remove(); node=parent; } if(node&&node!==clone && node.children.length<=1 && !text(node)) node.remove(); else image.remove(); return; } image.loading='eager'; image.removeAttribute('srcset'); image.removeAttribute('data-srcset'); image.src=state.url; }); };
  if (targetType === 'answer') { const titleClone=title.cloneNode(true), answerClone=source.cloneNode(true); transfer(title,titleClone,titleStates); transfer(source,answerClone,answerStates); wrapper.append(titleClone,answerClone); }
  else { const articleClone=source.cloneNode(true); transfer(source,articleClone,answerStates); wrapper.append(articleClone); }
  document.body.append(wrapper); await sleep(250);
  for (let attempt=0;attempt<24;attempt++){ if(Array.from(wrapper.querySelectorAll('img')).every(pixels)) break; await sleep(250); }
  const before=wrapper.querySelectorAll('img').length; wrapper.querySelectorAll('img').forEach(image=>{if(!pixels(image)){image.removeAttribute('src');image.removeAttribute('srcset');image.style.removeProperty('height');image.style.removeProperty('min-height');image.style.removeProperty('aspect-ratio');image.remove();}});
  const root=wrapper.getBoundingClientRect(); const boundaries=[]; for(const selector of ['h1','h2','h3','h4','h5','h6','p','blockquote','li','pre','table','figure','img','.RichContent-inner','.QuestionAnswer-content','.Post-RichTextContainer']) for(const node of wrapper.querySelectorAll(selector)){const rect=node.getBoundingClientRect();if(rect.height>1)boundaries.push({y:Math.round(rect.bottom-root.top),kind:selector});}
  return {ok:true,selector:'[data-social-capture-zhihu="true"]',title:targetType==='answer'?text(title):text(wrapper.querySelector('.Post-Header-title,h1')),imageCount:wrapper.querySelectorAll('img').length,removedImageCount:Math.max(0,before-wrapper.querySelectorAll('img').length),mode,clip:{x:root.left,y:root.top,width:Math.ceil(root.width),height:Math.ceil(Math.max(root.height,wrapper.scrollHeight))},boundaries};
}
"""
