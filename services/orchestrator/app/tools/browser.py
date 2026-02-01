from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright

from ..config import settings
from .base import ToolContext, ToolSpec, register


_PLAYWRIGHT_INSTALL_LOCK = threading.Lock()
_PLAYWRIGHT_INSTALL_DONE = False


def _hidden_process_kwargs() -> Dict[str, Any]:
    """
    Best-effort: prevent child console windows from flashing on Windows (e.g. Playwright driver node.exe).
    """
    if os.name != "nt":
        return {}
    # Hide console windows for subprocesses spawned from our windowed desktop EXE.
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return {"startupinfo": startupinfo, "creationflags": creationflags}


def _maybe_install_playwright_chromium(*, reason: str) -> None:
    """
    Playwright's Python package ships the driver, but browsers are downloaded separately.
    For desktop UX, auto-install Chromium on first use (best-effort).
    """
    global _PLAYWRIGHT_INSTALL_DONE
    if _PLAYWRIGHT_INSTALL_DONE:
        return

    # Allow opting out for constrained environments.
    if (os.getenv("OWB_PLAYWRIGHT_AUTO_INSTALL") or "true").strip().lower() not in ("1", "true", "yes", "y", "on"):
        raise RuntimeError(
            "Playwright Chromium browser is not installed. "
            "Run `python -m playwright install chromium` (or set OWB_PLAYWRIGHT_AUTO_INSTALL=true). "
            f"Reason: {reason}"
        )

    with _PLAYWRIGHT_INSTALL_LOCK:
        if _PLAYWRIGHT_INSTALL_DONE:
            return

        try:
            from playwright._impl._driver import compute_driver_executable, get_driver_env

            driver_executable, driver_cli = compute_driver_executable()
            env = os.environ.copy()
            env.update(get_driver_env())

            # Install only Chromium (smallest surface for our tools).
            proc = subprocess.run(  # noqa: S603,S607 - intended local install command
                [driver_executable, driver_cli, "install", "chromium"],
                env=env,
                capture_output=True,
                text=True,
                **_hidden_process_kwargs(),
            )
            if proc.returncode != 0:
                msg = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(f"playwright install chromium failed (code={proc.returncode}): {msg[:1200]}")
        except Exception as e:
            raise RuntimeError(
                "Playwright Chromium browser is required for browser.* tools, but auto-install failed. "
                "Please run `python -m playwright install chromium` manually and retry."
            ) from e

        _PLAYWRIGHT_INSTALL_DONE = True


async def _launch_chromium(p, *, headless: bool):
    try:
        return await p.chromium.launch(headless=headless)
    except Exception as e:  # noqa: BLE001 - probe for missing browser binaries
        msg = str(e)
        if ("Executable doesn't exist" in msg) or ("playwright install" in msg) or ("browser has been downloaded" in msg):
            _maybe_install_playwright_chromium(reason=msg[:500])
            return await p.chromium.launch(headless=headless)
        raise


async def _open(url: str) -> Dict[str, Any]:
    async with async_playwright() as p:
        browser = await _launch_chromium(p, headless=settings.browser_headless)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(settings.browser_timeout_ms)
        try:
            await page.goto(url, wait_until="domcontentloaded")
            title = await page.title()
            final_url = page.url
            return {"ok": True, "title": title, "final_url": final_url}
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass


async def _extract(url: str, selector: Optional[str], max_chars: int) -> Dict[str, Any]:
    async with async_playwright() as p:
        browser = await _launch_chromium(p, headless=settings.browser_headless)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(settings.browser_timeout_ms)
        try:
            await page.goto(url, wait_until="domcontentloaded")
            if selector:
                el = await page.query_selector(selector)
                text = await el.inner_text() if el else ""
            else:
                text = await page.inner_text("body")
            if len(text) > max_chars:
                text = text[:max_chars]
                truncated = True
            else:
                truncated = False
            return {"ok": True, "url": page.url, "selector": selector, "truncated": truncated, "text": text}
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass


async def _screenshot(url: str, out_path: Path, full_page: bool) -> Dict[str, Any]:
    async with async_playwright() as p:
        browser = await _launch_chromium(p, headless=settings.browser_headless)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(settings.browser_timeout_ms)
        try:
            await page.goto(url, wait_until="domcontentloaded")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(out_path), full_page=full_page)
            return {"ok": True, "url": page.url, "path": str(out_path)}
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass


async def _click_flow(url: str, actions: List[Dict[str, Any]], extract_selector: Optional[str], max_chars: int) -> Dict[str, Any]:
    async with async_playwright() as p:
        browser = await _launch_chromium(p, headless=settings.browser_headless)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(settings.browser_timeout_ms)
        try:
            await page.goto(url, wait_until="domcontentloaded")
            for a in actions:
                kind = a.get("type")
                if kind == "click":
                    sel = a.get("selector")
                    if not sel:
                        raise ValueError("click action requires selector")
                    await page.click(sel)
                elif kind == "fill":
                    sel = a.get("selector")
                    value = a.get("value", "")
                    await page.fill(sel, value)
                elif kind == "press":
                    key = a.get("key", "Enter")
                    await page.keyboard.press(key)
                elif kind == "wait":
                    ms = int(a.get("ms", 1000))
                    await page.wait_for_timeout(ms)
                elif kind == "goto":
                    new_url = a.get("url")
                    if not new_url:
                        raise ValueError("goto action requires url")
                    await page.goto(new_url, wait_until="domcontentloaded")
                else:
                    raise ValueError(f"unknown browser action type: {kind}")
            if extract_selector:
                el = await page.query_selector(extract_selector)
                text = await el.inner_text() if el else ""
            else:
                text = await page.inner_text("body")
            if len(text) > max_chars:
                text = text[:max_chars]
                truncated = True
            else:
                truncated = False
            return {"ok": True, "url": page.url, "truncated": truncated, "text": text}
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass


def browser_open(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.browser_enabled:
        raise PermissionError("browser tool disabled by server config")
    url = args["url"]
    return asyncio.run(_open(url))


def browser_extract(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.browser_enabled:
        raise PermissionError("browser tool disabled by server config")
    url = args["url"]
    selector = args.get("selector")
    max_chars = int(args.get("max_chars", 20000))
    return asyncio.run(_extract(url, selector, max_chars))


def browser_screenshot(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.browser_enabled:
        raise PermissionError("browser tool disabled by server config")
    url = args["url"]
    full_page = bool(args.get("full_page", True))
    filename = args.get("filename", "screenshot.png")
    out_path = settings.artifacts_dir / ctx.task_id / ctx.step_id / filename
    return asyncio.run(_screenshot(url, out_path, full_page))


def browser_click(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.browser_enabled:
        raise PermissionError("browser tool disabled by server config")
    url = args["url"]
    actions = args.get("actions") or []
    extract_selector = args.get("extract_selector")
    max_chars = int(args.get("max_chars", 20000))
    return asyncio.run(_click_flow(url, actions, extract_selector, max_chars))


def register_browser_tools() -> None:
    register(
        ToolSpec(
            name="browser.open",
            description="Open a URL in a headless browser and return title and final URL.",
            json_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            func=browser_open,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="browser.extract",
            description="Open a URL and extract readable text from body or a CSS selector.",
            json_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "selector": {"type": "string", "description": "optional CSS selector"},
                    "max_chars": {"type": "integer", "default": 20000},
                },
                "required": ["url"],
            },
            func=browser_extract,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="browser.screenshot",
            description="Open a URL and save a screenshot as an artifact. Returns the artifact path.",
            json_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "full_page": {"type": "boolean", "default": True},
                    "filename": {"type": "string", "default": "screenshot.png"},
                },
                "required": ["url"],
            },
            func=browser_screenshot,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="browser.click",
            description="Open a URL and perform a sequence of UI actions (click/fill/press/wait/goto), optionally extracting text after.",
            json_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["click", "fill", "press", "wait", "goto"]},
                                "selector": {"type": "string"},
                                "value": {"type": "string"},
                                "key": {"type": "string"},
                                "ms": {"type": "integer"},
                                "url": {"type": "string"},
                            },
                            "required": ["type"],
                        },
                    },
                    "extract_selector": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 20000},
                },
                "required": ["url", "actions"],
            },
            func=browser_click,
            risky=True,
        )
    )
