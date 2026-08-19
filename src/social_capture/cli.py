"""Command line entry point for social-capture."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from .auth import load_auth_material, redact_source_url, redact_text
from .browser import BrowserSession, normalize_cdp_url
from .console import configure_utf8_console
from .errors import InputError, SocialCaptureError, error_kind
from .models import CaptureOptions, CaptureResult
from .output import OutputWriter
from .registry import (
    detect_provider,
    get_provider,
    list_providers,
    provider_health,
    search_backend_status,
)

PLATFORMS = tuple(provider.name for provider in list_providers())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="social-capture",
        description="Capture focused cards from Weibo, Zhihu, X, Xiaohongshu and Douyin.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    providers = sub.add_parser("providers", help="列出内置截图平台")
    providers.add_argument("--json", action="store_true", help="输出 JSON")

    backends = sub.add_parser("search-backends", help="列出可选的外部搜索后端（只读提示）")
    backends.add_argument("--platform", choices=("all", *PLATFORMS), default="all")
    backends.add_argument("--json", action="store_true", help="输出 JSON")

    doctor = sub.add_parser("doctor", help="检查本地依赖和可选后端，不创建输出目录")
    doctor.add_argument("--platform", choices=("all", *PLATFORMS), default="all")
    doctor.add_argument("--cdp", "--cdp-port", dest="cdp", default="http://127.0.0.1:9221")
    doctor.add_argument("--json", action="store_true", help="输出 JSON")

    auth = sub.add_parser("auth", help="检查平台认证和 Chrome CDP")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_check = auth_sub.add_parser("check", help="检查登录态来源，不输出 Cookie")
    auth_check.add_argument("--platform", choices=PLATFORMS, required=True)
    auth_check.add_argument("--cookie-file", type=Path)
    auth_check.add_argument("--cdp", "--cdp-port", dest="cdp", default="http://127.0.0.1:9221")
    auth_check.add_argument("--json", action="store_true")

    capture = sub.add_parser("capture", help="截图一个 URL 或 --input 文本文件中的 URL")
    capture.add_argument("reference", nargs="?", help="平台详情 URL；--input 与其二选一")
    capture.add_argument("--input", type=Path, help="每行一个 URL/ID，空行和 # 注释会忽略")
    capture.add_argument("--platform", choices=("auto", *PLATFORMS), default="auto")
    capture.add_argument("--output-dir", type=Path, required=True, help="必填：用户指定的输出目录")
    capture.add_argument("--cdp", "--cdp-port", dest="cdp", default="http://127.0.0.1:9221")
    capture.add_argument("--cookie-file", type=Path)
    capture.add_argument("--wait", type=float, default=3.0, metavar="SECONDS")
    capture.add_argument("--overlap", type=int, default=64, metavar="PIXELS")
    capture.add_argument("--max-ratio", default="9:16", help="长内容分片最大宽高比，默认 9:16")
    capture.add_argument("--overwrite", action="store_true", help="允许覆盖目标目录中的同名结果")
    capture.add_argument("--x-cut-stats", action="store_true", help="X：隐藏互动统计区")
    capture.add_argument("--xhs-comments", type=int, default=8, metavar="N", help="小红书：保留前 N 条评论（默认 8，0 表示隐藏）")
    capture.add_argument("--xhs-all-images", action="store_true", help="小红书：遍历可见轮播图")
    capture.add_argument("--douyin-rows", type=int, default=2, choices=range(1, 5), metavar="1-4")
    capture.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    return parser


def _ratio(value: str) -> tuple[int, int]:
    try:
        left, right = value.split(":", 1)
        width, height = int(left), int(right)
        if width <= 0 or height <= 0:
            raise ValueError
        return width, height
    except (ValueError, AttributeError):
        raise InputError("--max-ratio 必须是正整数比例，例如 9:16")


def _read_references(args: argparse.Namespace) -> list[str]:
    if bool(args.reference) == bool(args.input):
        raise InputError("capture 请提供一个 URL/ID，或使用 --input 文件（二选一）")
    if args.reference:
        return [str(args.reference).strip()]
    try:
        values = args.input.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise InputError(f"无法读取 --input 文件: {args.input}") from exc
    result = [line.strip() for line in values if line.strip() and not line.lstrip().startswith("#")]
    if not result:
        raise InputError("--input 文件没有可用输入")
    return result


def _provider_for(value: str, name: str):
    return detect_provider(value) if name == "auto" else get_provider(name)


def _validate_platform_options(args: argparse.Namespace, values: list[str]) -> None:
    if args.platform != "auto":
        names = {args.platform}
    else:
        names: set[str] = set()
        for value in values:
            try:
                names.add(_provider_for(value, args.platform).name)
            except (InputError, ValueError):
                # Unknown values must reach the normal per-item error path so
                # a batch still gets an explicit failure manifest.
                continue
    if not names:
        return
    if args.x_cut_stats and names != {"x"}:
        raise InputError("--x-cut-stats 只适用于 X；auto 批量不能混入其他平台")
    if (args.xhs_all_images or args.xhs_comments != 8) and names != {"xiaohongshu"}:
        raise InputError("--xhs-comments/--xhs-all-images 只适用于小红书；auto 批量必须全是小红书")
    if args.douyin_rows != 2 and names != {"douyin"}:
        raise InputError("--douyin-rows 非默认值只适用于抖音；auto 批量必须全是抖音")


async def _capture(args: argparse.Namespace) -> int:
    width, height = _ratio(args.max_ratio)
    values = _read_references(args)
    _validate_platform_options(args, values)
    writer = OutputWriter(args.output_dir, overwrite=args.overwrite)
    writer.preflight()
    items: list[CaptureResult] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        provider_name = args.platform
        try:
            provider = _provider_for(value, args.platform)
            reference = provider.parse_reference(value)
            key = (reference.platform, reference.content_id)
            if key in seen:
                continue
            seen.add(key)
            material = load_auth_material(provider.name, cookie_file=args.cookie_file)
            options = CaptureOptions(
                output_dir=writer.output_dir,
                cdp_url=normalize_cdp_url(args.cdp),
                cookie_header=material.cookie_header if material else None,
                wait_seconds=max(0.0, args.wait),
                overlap=max(0, args.overlap),
                max_ratio_width=width,
                max_ratio_height=height,
                overwrite=bool(args.overwrite),
                x_cut_stats=bool(args.x_cut_stats),
                xhs_comments=max(0, args.xhs_comments),
                xhs_all_images=bool(args.xhs_all_images),
                douyin_rows=int(args.douyin_rows),
            )
            async with BrowserSession(options.cdp_url, cookie_header=options.cookie_header) as browser:
                artifact = await provider.capture(browser, reference, options)
            item = writer.write_artifact(reference, artifact, options)
            items.append(item)
            provider_name = provider.name
        except Exception as exc:
            if isinstance(exc, SocialCaptureError):
                message = str(exc)
                kind = error_kind(exc)
            else:
                message = redact_text(str(exc)) or exc.__class__.__name__
                kind = "capture"
            item = CaptureResult(
                input_value=value,
                platform=provider_name if provider_name != "auto" else "unknown",
                content_id="",
                source_url=redact_source_url(value),
                content_type="unknown",
                status="error",
                error=message,
                error_kind=kind,
            )
            items.append(item)
    manifest = writer.write_manifest(items, extra={"ratio": f"{width}:{height}", "overlap": max(0, args.overlap)})
    summary = {
        "manifest": str(manifest),
        "count": len(items),
        "success_count": sum(item.status == "ok" for item in items),
        "failed_count": sum(item.status != "ok" for item in items),
    }
    print(json.dumps(summary, ensure_ascii=False) if args.json else f"manifest: {manifest}")
    return 0 if summary["failed_count"] == 0 else (3 if summary["success_count"] else 2)


def _print_providers(as_json: bool) -> int:
    rows = [
        {
            "name": p.name,
            "hosts": p.hosts,
            "capabilities": p.capabilities,
            "search_backend": search_backend_status(p.name)[0],
        }
        for p in list_providers()
    ]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for row in rows:
            hint = row["search_backend"]
            print(f"{row['name']}: {', '.join(row['capabilities'])}; search={hint['name']} (external)")
    return 0


def _print_search_backends(platform: str, as_json: bool) -> int:
    rows = search_backend_status(None if platform == "all" else platform)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for row in rows:
            state = "installed" if row["installed"] else "not installed"
            print(f"{row['platform']}: {row['name']} ({state}) - {row['repo_url']}")
            print(f"  install: {row['install_hint']}")
            print(f"  use: {row['command_hint']}")
            print(f"  license: {row['license_note']}")
    return 0


async def _probe_cdp(endpoint: str) -> dict[str, Any]:
    normalized = normalize_cdp_url(endpoint)
    try:
        async with BrowserSession(normalized):
            return {"available": True, "endpoint": normalized, "message": "Chrome CDP 可连接"}
    except Exception as exc:
        return {"available": False, "endpoint": normalized, "message": redact_text(str(exc))}


def _doctor(args: argparse.Namespace) -> int:
    platform = None if args.platform == "all" else args.platform
    rows = [
        {"provider": h.provider, "available": h.available, "message": h.message, "capabilities": h.capabilities}
        for h in provider_health()
        if platform is None or h.provider == platform
    ]
    deps = {"playwright": importlib.util.find_spec("playwright") is not None, "pillow": importlib.util.find_spec("PIL") is not None}
    browser = asyncio.run(_probe_cdp(args.cdp))
    payload = {"providers": rows, "dependencies": deps, "browser": browser, "search_backends": search_backend_status(platform)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"playwright: {'ok' if deps['playwright'] else 'missing'}")
        print(f"Pillow: {'ok' if deps['pillow'] else 'missing'}")
        print(f"browser: {'ok' if browser['available'] else 'unavailable'} ({browser['endpoint']})")
        for row in rows:
            print(f"{row['provider']}: {'ok' if row['available'] else 'error'}")
        print("optional search backends:")
        for row in payload["search_backends"]:
            print(f"  {row['platform']}: {'installed' if row['installed'] else 'not installed'}")
    return 0 if all(deps.values()) and bool(browser["available"]) else 2


async def _auth_check(args: argparse.Namespace) -> int:
    material = load_auth_material(args.platform, cookie_file=args.cookie_file)
    payload: dict[str, Any] = {"platform": args.platform, "cookie_source": material.source if material else None}
    try:
        async with BrowserSession(normalize_cdp_url(args.cdp), cookie_header=material.cookie_header if material else None):
            payload.update({"cdp": True, "message": "Chrome CDP 可连接"})
    except Exception as exc:
        payload.update({"cdp": False, "message": redact_text(str(exc))})
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"platform: {args.platform}")
        print(f"cookie: {payload['cookie_source'] or 'not provided (using Chrome session)'}")
        print(f"cdp: {'ok' if payload['cdp'] else 'error'}")
    return 0 if payload["cdp"] else 2


def main(argv: list[str] | None = None) -> int:
    configure_utf8_console()
    args = _parser().parse_args(argv)
    try:
        if args.command == "providers":
            return _print_providers(args.json)
        if args.command == "search-backends":
            return _print_search_backends(args.platform, args.json)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "auth":
            return asyncio.run(_auth_check(args))
        if args.command == "capture":
            return asyncio.run(_capture(args))
        return 2
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 2
    except Exception as exc:
        message = redact_text(str(exc)) or exc.__class__.__name__
        print(message, file=sys.stderr)
        return exc.exit_code if isinstance(exc, SocialCaptureError) else 2


if __name__ == "__main__":
    raise SystemExit(main())
