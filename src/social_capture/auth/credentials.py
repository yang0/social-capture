from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..errors import AuthError

COOKIE_ENV_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "weibo": ("WEIBO_COOKIE", "SOCIAL_CAPTURE_COOKIE"),
    "zhihu": ("ZHIHU_COOKIE", "SOCIAL_CAPTURE_COOKIE"),
    "x": ("X_COOKIE", "TWITTER_COOKIE", "SOCIAL_CAPTURE_COOKIE"),
    "xiaohongshu": ("XHS_COOKIE", "XIAOHONGSHU_COOKIE", "SOCIAL_CAPTURE_COOKIE"),
    "douyin": ("DOUYIN_COOKIE", "SOCIAL_CAPTURE_COOKIE"),
}

SENSITIVE_QUERY_KEYS = {
    "xsec_token",
    "token",
    "access_token",
    "auth_token",
    "session",
    "sessionid",
    "signature",
    "sign",
    "sig",
    "cookie",
    "code",
}
_SECRET_PATTERN = re.compile(
    r"(?i)(xsec_token|access_token|auth_token|sessionid|signature|sig|token|cookie)\s*"
    r"([=:])\s*([^;&\s]+)"
)


@dataclass(frozen=True)
class AuthMaterial:
    """Cookie header plus provenance (provenance is safe to expose)."""

    cookie_header: str
    source: str


def parse_cookie_header(value: str) -> dict[str, str]:
    """Parse a Cookie header, ignoring malformed pieces and empty names."""

    result: dict[str, str] = {}
    for piece in str(value or "").split(";"):
        if "=" not in piece:
            continue
        name, cookie_value = piece.split("=", 1)
        name = name.strip()
        if name:
            result[name] = cookie_value.strip()
    return result


def _cookie_header_from_json(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        # Accept {"name": "value"} and {"cookies": [{...}]} exports.
        if "cookies" in value:
            return _cookie_header_from_json(value["cookies"])
        pairs = [(str(k), str(v)) for k, v in value.items() if k and v is not None]
        return "; ".join(f"{k}={v}" for k, v in pairs) or None
    if isinstance(value, list):
        pairs: list[tuple[str, str]] = []
        for item in value:
            if isinstance(item, Mapping) and item.get("name") and item.get("value") is not None:
                pairs.append((str(item["name"]), str(item["value"])))
        return "; ".join(f"{k}={v}" for k, v in pairs) or None
    return None


def _read_cookie_file(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise AuthError(f"无法读取 Cookie 文件: {path}", code="cookie-file-error") from exc
    if not raw:
        raise AuthError("Cookie 文件为空", code="cookie-file-empty")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    header = _cookie_header_from_json(parsed)
    if not header or not parse_cookie_header(header):
        raise AuthError("Cookie 文件格式无法识别", code="cookie-file-invalid")
    return header


def load_auth_material(
    platform: str,
    *,
    cookie_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> AuthMaterial | None:
    """Load auth using cookie-file > platform env precedence."""

    env = os.environ if environ is None else environ
    if cookie_file:
        path = Path(cookie_file).expanduser()
        if not path.is_file():
            raise AuthError(f"Cookie 文件不存在: {path}", code="cookie-file-missing")
        return AuthMaterial(_read_cookie_file(path), "cookie-file")
    for env_name in COOKIE_ENV_BY_PLATFORM.get(platform, ("SOCIAL_CAPTURE_COOKIE",)):
        value = str(env.get(env_name, "") or "").strip()
        if value:
            if not parse_cookie_header(value):
                raise AuthError(f"环境变量 {env_name} 不包含有效 Cookie", code="cookie-invalid")
            return AuthMaterial(value, f"env:{env_name}")
    return None


def redact_source_url(value: str) -> str:
    """Remove tracking and credential-like query values while preserving URL shape."""

    raw = str(value or "")
    try:
        parts = urlsplit(raw)
        query: list[tuple[str, str]] = []
        for key, item in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in SENSITIVE_QUERY_KEYS or any(
                marker in key.lower() for marker in ("token", "cookie", "session", "sign")
            ):
                continue
            query.append((key, item))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    except ValueError:
        return raw


def redact_text(value: str) -> str:
    """Redact common key/value secret forms in exception text."""

    text = _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", str(value or ""))
    # Do not emit a full Cookie header even when a provider accidentally includes it.
    if len(text) > 2000:
        text = text[:2000] + "…"
    return text


def cookie_dict_for_domain(cookie_header: str, domain: str) -> list[dict[str, str]]:
    """Convert a Cookie header into Playwright cookie records."""

    if not cookie_header:
        return []
    parsed = parse_cookie_header(cookie_header)
    return [{"name": name, "value": value, "domain": domain, "path": "/"} for name, value in parsed.items()]
