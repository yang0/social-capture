"""Stable errors and exit-code mapping."""

from __future__ import annotations


class SocialCaptureError(RuntimeError):
    code = "capture-failed"
    exit_code = 2

    def __init__(self, message: str, *, code: str | None = None, exit_code: int | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code


class InputError(SocialCaptureError):
    code = "invalid-input"


class AuthError(SocialCaptureError):
    code = "auth-required"


class BlockedError(SocialCaptureError):
    code = "security-block"


class TargetNotFoundError(SocialCaptureError):
    code = "target-not-found"


class OutputExistsError(SocialCaptureError):
    code = "output-exists"


class BrowserError(SocialCaptureError):
    code = "cdp-unavailable"


class ImageError(SocialCaptureError):
    code = "invalid-image"


def error_kind(error: BaseException) -> str:
    if isinstance(error, AuthError | BlockedError):
        return "blocked"
    if isinstance(error, TargetNotFoundError):
        return "not-found"
    if isinstance(error, InputError):
        return "input"
    if isinstance(error, OutputExistsError):
        return "output"
    if isinstance(error, BrowserError):
        return "browser"
    if isinstance(error, ImageError):
        return "image"
    return "capture"
