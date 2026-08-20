"""One safe browser abstraction for all providers.

The normal path attaches to an already-running Chrome through Playwright's
``connect_over_cdp``. A temporary, CLI-owned Chrome is only started when a
cookie was explicitly supplied and the configured endpoint is unavailable.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from ..auth import cookie_dict_for_domain
from ..errors import BrowserError


def normalize_cdp_url(value: str | int | None) -> str:
    raw = str(value or "http://127.0.0.1:9221").strip()
    if raw.isdigit():
        return f"http://127.0.0.1:{raw}"
    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw
    return raw.rstrip("/")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _chrome_candidates() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("CHROME_PATH", "CHROMIUM_PATH"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))
    candidates.extend(
        [
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    )
    result: list[Path] = []
    for item in candidates:
        if item and item.is_file() and item not in result:
            result.append(item)
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser", "msedge"):
        resolved = shutil.which(name)
        if resolved:
            path = Path(resolved)
            if path not in result:
                result.append(path)
    return result


class TemporaryChrome:
    """Launch and clean up a browser owned by this process."""

    def __init__(self, *, cookie_header: str | None = None):
        self.cookie_header = cookie_header
        self.port = _free_port()
        self.profile_dir = Path(tempfile.mkdtemp(prefix="social-capture-chrome-"))
        self.process: subprocess.Popen[Any] | None = None

    def start(self) -> str:
        candidates = _chrome_candidates()
        if not candidates:
            self.close()
            raise BrowserError(
                "找不到 Chrome/Chromium；请启动带远程调试端口的 Chrome，或设置 CHROME_PATH",
                code="chrome-not-found",
            )
        command = [
            str(candidates[0]),
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except OSError as exc:
            self.close()
            raise BrowserError("无法启动 CLI 临时 Chrome", code="chrome-launch-failed") from exc
        url = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{url}/json/version", timeout=0.5):
                    return url
            except OSError:
                time.sleep(0.15)
        self.close()
        raise BrowserError("临时 Chrome 未在限定时间内打开 CDP", code="chrome-cdp-timeout")

    def close(self) -> None:
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        shutil.rmtree(self.profile_dir, ignore_errors=True)


class BrowserSession:
    def __init__(
        self,
        cdp_url: str,
        *,
        cookie_header: str | None = None,
        allow_temporary: bool = False,
    ):
        self.cdp_url = normalize_cdp_url(cdp_url)
        self.cookie_header = cookie_header
        # Existing social-platform capture keeps the strict CDP contract.
        # Generic public webpages may explicitly opt into a clean temporary
        # Chrome when no user-owned CDP endpoint is available.
        self.allow_temporary = bool(allow_temporary)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._pages: list[Any] = []
        self._temporary: TemporaryChrome | None = None

    async def __aenter__(self) -> BrowserSession:
        try:
            await self._connect(self.cdp_url)
        except BrowserError:
            if not (self.cookie_header or self.allow_temporary):
                raise
            self._temporary = TemporaryChrome(cookie_header=self.cookie_header)
            try:
                temporary_url = await asyncio.to_thread(self._temporary.start)
                await self._connect(temporary_url)
            except Exception:
                # __aexit__ is not called when __aenter__ fails. Explicitly
                # tear down the CLI-owned browser and Playwright transport so
                # a failed fallback never leaves a process/profile behind.
                if self._playwright:
                    try:
                        await self._playwright.stop()
                    except Exception:
                        pass
                self._playwright = self._browser = self._context = None
                if self._temporary:
                    await asyncio.to_thread(self._temporary.close)
                    self._temporary = None
                raise
        return self

    async def _connect(self, endpoint: str) -> None:
        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserError(
                "未安装 Playwright；请执行 python -m pip install -e .",
                code="playwright-missing",
            ) from exc
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
            else:
                self._context = await self._browser.new_context()
        except Exception as exc:
            if self._playwright is not None:
                await self._playwright.stop()
            self._playwright = self._browser = self._context = None
            if isinstance(exc, PlaywrightError):
                raise BrowserError(f"无法连接 Chrome CDP: {endpoint}", code="cdp-unavailable") from exc
            raise BrowserError(f"无法连接 Chrome CDP: {endpoint}", code="cdp-unavailable") from exc

    async def new_page(self) -> Any:
        if self._context is None:
            raise BrowserError("浏览器会话尚未连接", code="browser-not-ready")
        page = await self._context.new_page()
        self._pages.append(page)
        return page

    async def apply_cookies(self, domain: str) -> None:
        """Apply the explicitly supplied Cookie header for one site."""

        if self._context is None or not self.cookie_header:
            return
        domain = str(domain or "").strip().lstrip(".")
        if not domain or "." not in domain:
            return
        await self._context.add_cookies(cookie_dict_for_domain(self.cookie_header, f".{domain}"))

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for page in reversed(self._pages):
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
        if self._temporary:
            # The connected browser belongs to us, but closing pages above is
            # still enough for a clean handoff. Stop the Playwright transport.
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        if self._temporary:
            await asyncio.to_thread(self._temporary.close)
        self._pages.clear()

    async def check(self) -> tuple[bool, str]:
        """Read-only health check used by ``doctor`` and ``auth check``."""

        try:
            await self._connect(self.cdp_url)
        except BrowserError as exc:
            return False, str(exc)
        return True, "Chrome CDP 可连接"
