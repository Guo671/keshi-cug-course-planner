"""Local-browser fallback; never modifies Windows trust or browser security settings."""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import subprocess
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

LOGGER = logging.getLogger("keshi.desktop")


class BrowserSession:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.ready = threading.Event()
        self.cancel = threading.Event()

    def attach(self, app, origin):
        from fastapi import Request
        from fastapi.responses import Response
        from starlette.routing import Route

        async def acknowledge(request: Request):
            if request.headers.get("origin") != origin:
                return Response(status_code=403)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 2048:
                    return Response(status_code=413)
            try:
                payload = json.loads(body)
                token = payload.get("token") if isinstance(payload, dict) else None
            except (ValueError, UnicodeError):
                return Response(status_code=400)
            if (
                not isinstance(token, str)
                or not token.isascii()
                or not hmac.compare_digest(token, self.token)
            ):
                return Response(status_code=403)
            self.ready.set()
            return Response(status_code=204)

        # Insert before the catch-all StaticFiles mount. Not a public application API.
        app.router.routes.insert(0, Route("/_keshi/browser-ready", acknowledge, methods=["POST"]))


def _registered_browsers():
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for exe, label in [("msedge.exe", "Microsoft Edge"), ("chrome.exe", "Google Chrome")]:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    key = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
                    with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                        value, _ = winreg.QueryValueEx(handle, "")
                    if isinstance(value, str):
                        found.append((label, Path(os.path.expandvars(value.strip('"')))))
                except OSError:
                    continue
    return found


def find_browsers(environ=None):
    environ = os.environ if environ is None else environ
    candidates = _registered_browsers()
    for label, relative in [
        ("Microsoft Edge", "Microsoft/Edge/Application/msedge.exe"),
        ("Google Chrome", "Google/Chrome/Application/chrome.exe"),
    ]:
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if environ.get(key):
                candidates.append((label, Path(environ[key]) / relative))
    result, seen = [], set()
    for label in ("Microsoft Edge", "Google Chrome"):
        for name, path in candidates:
            identity = str(path).casefold()
            if name == label and identity not in seen and path.is_file():
                result.append((name, path))
                seen.add(identity)
    return result


def open_browser(url, session, *, candidates=None, spawn=None, open_default=None, wait_seconds=8.0):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise ValueError("Browser mode only opens the reserved loopback service")
    candidates = find_browsers() if candidates is None else candidates
    spawn = subprocess.Popen if spawn is None else spawn
    open_default = webbrowser.open if open_default is None else open_default
    target = url.rstrip("/") + "/#keshi-launch=" + session.token
    session.ready.clear()
    for label, path in candidates:
        if session.cancel.is_set():
            return None
        try:
            spawn(
                [str(path), "--new-window", target],
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            LOGGER.warning("无法打开 %s，尝试其他浏览器", label, exc_info=True)
            continue
        if session.ready.wait(wait_seconds):
            LOGGER.info("本机浏览器界面加载成功：%s", label)
            return label
        LOGGER.warning("%s 未在时限内确认界面加载，尝试其他浏览器", label)
    if session.cancel.is_set():
        return None
    try:
        if open_default(target, new=1) and session.ready.wait(wait_seconds):
            LOGGER.info("默认浏览器界面加载成功")
            return "默认浏览器"
    except Exception:
        LOGGER.warning("默认浏览器打开失败", exc_info=True)
    return None


def window_dependency_blocked(root):
    file = Path(root) / "pythonnet/runtime/Python.Runtime.dll"
    try:
        with Path(str(file) + ":Zone.Identifier").open(
            encoding="utf-8", errors="replace"
        ) as stream:
            text = stream.read(4096)
        return any(
            line.strip().replace(" ", "") in ("ZoneId=3", "ZoneId=4") for line in text.splitlines()
        )
    except OSError:
        return False


def prefer_browser(paths):
    try:
        value = json.loads((paths.data_root / "startup.json").read_text(encoding="utf-8"))
        return isinstance(value, dict) and value.get("browser_mode") is True
    except (OSError, ValueError):
        return False


def remember_browser(paths):
    try:
        paths.data_root.mkdir(parents=True, exist_ok=True)
        target = paths.data_root / "startup.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text('{"browser_mode": true}\n', encoding="utf-8")
        temporary.replace(target)
    except OSError:
        LOGGER.warning("未能保存兼容模式偏好；当前仍可继续使用", exc_info=True)


def run_browser_mode(paths, server, session, reason):
    from desktop.browser_control import show_browser_control

    LOGGER.warning("进入本机浏览器兼容模式：%s", reason)
    label = open_browser(server.url, session)
    if label:
        remember_browser(paths)
    message = (
        f"已在{label}打开课石。"
        if label
        else "尚未收到浏览器页面的加载确认。请点“重新打开页面”，或复制下方地址到Edge/Chrome。"
    )
    try:
        show_browser_control(server.url, message, lambda: open_browser(server.url, session))
    finally:
        session.cancel.set()
