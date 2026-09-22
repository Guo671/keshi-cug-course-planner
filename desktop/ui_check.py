"""Opt-in genuine UI checks against the shipped resources, with isolated user data."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from desktop.browser_mode import (
    BrowserSession,
    find_browsers,
    open_browser,
    window_dependency_blocked,
)
from desktop.runtime import (
    BackendServer,
    DesktopRuntimeError,
    RuntimePaths,
    reserve_backend_socket,
    seed_user_database,
    webview2_runtime_available,
)


def run_ui_check(data_root: Path, choice: str):
    from desktop.launcher import _configure_logging, _open_desktop_window

    paths = RuntimePaths.discover(data_root)
    _configure_logging(paths)
    paths.validate_resources()
    seed_user_database(paths)
    paths.configure_backend_environment()
    session = BrowserSession()
    server = BackendServer(reserve_backend_socket(), session)
    processes = []
    try:
        server.start()
        reason = None
        actual = choice
        if choice == "auto":
            if window_dependency_blocked(paths.resource_root):
                actual, reason = "edge", "internet-marked-window-dependency"
            elif not webview2_runtime_available():
                actual, reason = "edge", "missing-webview2"
            else:
                actual = "native"
        if actual == "native":
            try:
                _open_desktop_window(paths, server.url, check=True)
                return {
                    "status": "ok",
                    "mode": "native",
                    "page_loaded": True,
                    "frozen": bool(getattr(sys, "frozen", False)),
                }
            except Exception:
                if choice != "auto":
                    raise
                actual, reason = "edge", "native-window-failed"
        candidates = find_browsers()
        if choice != "auto":
            expected = "Microsoft Edge" if choice == "edge" else "Google Chrome"
            candidates = [(name, path) for name, path in candidates if name == expected]

        def spawn(args, **kwargs):
            directory = data_root / ("check-browser-" + str(len(processes)))
            command = [
                args[0],
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--user-data-dir=" + str(directory),
                args[-1],
            ]
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs
            )
            processes.append(process)
            return process

        label = open_browser(
            server.url,
            session,
            candidates=candidates,
            spawn=spawn,
            open_default=lambda *_a, **_k: False,
            wait_seconds=15,
        )
        if not label:
            raise DesktopRuntimeError(f"{choice} 浏览器未确认加载课石页面")
        return {
            "status": "ok",
            "mode": "browser",
            "browser": label,
            "page_loaded": session.ready.is_set(),
            "reason": reason,
            "frozen": bool(getattr(sys, "frozen", False)),
        }
    finally:
        for process in processes:
            if process.poll() is None:
                # Only our isolated headless test process; never an existing user browser.
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        server.stop()
        logger = logging.getLogger("keshi.desktop")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
