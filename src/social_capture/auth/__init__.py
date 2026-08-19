"""Credential loading and redaction helpers.

Credentials are intentionally represented only long enough to inject them into
the browser context. They are never put into a manifest or normal log line.
"""

from .credentials import (
    COOKIE_ENV_BY_PLATFORM,
    AuthMaterial,
    cookie_dict_for_domain,
    load_auth_material,
    parse_cookie_header,
    redact_source_url,
    redact_text,
)

__all__ = [
    "COOKIE_ENV_BY_PLATFORM",
    "AuthMaterial",
    "cookie_dict_for_domain",
    "load_auth_material",
    "parse_cookie_header",
    "redact_source_url",
    "redact_text",
]
