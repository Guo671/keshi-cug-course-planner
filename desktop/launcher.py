"""Command-line entry point and Windows desktop window lifecycle."""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import sys
import tempfile
import traceback
from collections.abc import Sequence
from logging.handlers import RotatingFileHandler
from pathlib import Path

from desktop import APP_NAME, APP_TITLE, APP_VERSION
from desktop.browser_mode import (
    BrowserSession,
    prefer_browser,
    run_browser_mode,
    window_dependency_blocked,
)
from desktop.runtime import (
    BackendServer,
    DesktopRuntimeError,
    RuntimePaths,
    SingleInstance,
    reserve_backend_socket,
    seed_user_database,
    smoke_test,
    webview2_runtime_available,
)

LOGGER = logging.getLogger("keshi.desktop")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} Windows 桌面程序")
    parser.add_argument("--version", action="store_true", help="显示版本后退出")
    parser.add_argument(
        "--check-ui",
        choices=["native", "edge", "chrome", "auto"],
        help="在隔离目录验证真实界面；仅用于诊断和发布验证",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--browser", action="store_true", help="直接在Edge/Chrome中使用本机课石")
    mode.add_argument("--desktop", action="store_true", help="优先重试原桌面窗口，失败仍转兼容模式")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="验证资源、首启数据库和本地服务后退出（不打开窗口）",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="覆盖用户数据目录；主要供测试和故障诊断使用",
    )
    return parser


def _configure_logging(paths: RuntimePaths) -> None:
    paths.log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        paths.log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    for existing_handler in LOGGER.handlers[:]:
        existing_handler.close()
        LOGGER.removeHandler(existing_handler)
    LOGGER.addHandler(handler)


def _show_native_message(title: str, message: str, *, error: bool = False) -> None:
    if sys.platform != "win32":
        print(f"{title}: {message}", file=sys.stderr)
        return
    import ctypes

    flags = 0x00000000 | (0x00000010 if error else 0x00000040)
    ctypes.windll.user32.MessageBoxW(None, message, title, flags)


def _run_smoke_test(data_dir: Path | None) -> int:
    if data_dir is not None or os.environ.get("CUG_PLANNER_DATA_DIR"):
        paths = RuntimePaths.discover(data_dir)
        _configure_logging(paths)
        result = smoke_test(paths)
    else:
        with tempfile.TemporaryDirectory(prefix="keshi-smoke-") as temporary_directory:
            paths = RuntimePaths.discover(Path(temporary_directory))
            _configure_logging(paths)
            result = smoke_test(paths)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def _run_gui(data_dir: Path | None, *, mode: str = "auto") -> int:
    if sys.platform != "win32":
        raise DesktopRuntimeError("课石桌面版当前仅支持 Windows 10/11 64 位系统。")

    paths = RuntimePaths.discover(data_dir)
    _configure_logging(paths)
    LOGGER.info(
        "启动 %s %s；资源目录=%s；数据目录=%s",
        APP_NAME,
        APP_VERSION,
        paths.resource_root,
        paths.data_root,
    )

    with SingleInstance():
        paths.validate_resources()
        created = seed_user_database(paths)
        LOGGER.info("用户数据库就绪；首次创建=%s；路径=%s", created, paths.database_path)
        paths.configure_backend_environment()

        reserved = reserve_backend_socket()
        server = BackendServer(reserved)
        session = BrowserSession()
        server.browser_session = session
        try:
            health = server.start()
            LOGGER.info(
                "本地服务已启动；port=%s；preferred=%s；health=%s",
                server.port,
                reserved.used_preferred_port,
                health,
            )

            reason = None
            if mode == "browser":
                reason = "使用浏览器启动入口"
            elif mode == "auto" and prefer_browser(paths):
                reason = "沿用此前可用的浏览器兼容模式"
            elif window_dependency_blocked(paths.resource_root):
                reason = "桌面窗口组件带有互联网来源标记"
            elif not webview2_runtime_available():
                reason = "未检测到可用的WebView2运行时"
            if reason:
                run_browser_mode(paths, server, session, reason)
            else:
                try:
                    _open_desktop_window(paths, server.url)
                except Exception:
                    LOGGER.exception("桌面窗口初始化失败，转为本机浏览器模式")
                    run_browser_mode(paths, server, session, "桌面窗口初始化失败")
        finally:
            server.stop()
            LOGGER.info("课石本地服务已停止")
    return 0


def _open_desktop_window(paths: RuntimePaths, url: str, *, check: bool = False) -> None:
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        APP_TITLE,
        url,
        width=1240,
        height=760,
        min_size=(960, 640),
        resizable=True,
        text_select=True,
        confirm_close=False,
        hidden=check,
    )
    import threading
    import time

    state = {"ready": False, "failed": False, "user_closed": False}

    def closing():
        if not state["failed"] and not check:
            state["user_closed"] = True

    def expired():
        if not state["ready"] and not state["user_closed"]:
            state["failed"] = True
            window.destroy()

    def loaded():
        if state["ready"] or state["user_closed"]:
            return
        deadline = time.monotonic() + 25
        try:
            while time.monotonic() < deadline and not state["user_closed"]:
                status = window.evaluate_js(
                    "({ready:window.keshiUiReady===true,failed:window.keshiUiFailed===true})"
                )
                if isinstance(status, dict) and status.get("failed"):
                    break
                if isinstance(status, dict) and status.get("ready"):
                    state["ready"] = True
                    timer.cancel()
                    break
                time.sleep(0.1)
            if not state["ready"] and not state["user_closed"]:
                state["failed"] = True
        except Exception:
            if not state["user_closed"]:
                state["failed"] = True
                LOGGER.exception("桌面页面初始化确认失败")
        finally:
            if check or state["failed"]:
                window.destroy()

    window.events.closing += closing
    window.events.loaded += loaded
    timer = threading.Timer(25, expired)
    timer.daemon = True
    timer.start()
    try:
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(paths.webview_storage_path),
            icon=str(paths.icon_path),
        )
    finally:
        timer.cancel()
    if state["failed"] or (not state["ready"] and (check or not state["user_closed"])):
        raise DesktopRuntimeError("桌面窗口未完成页面加载，改用本机浏览器模式")


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    arguments = _argument_parser().parse_args(argv)
    if arguments.version:
        print(f"Keshi {APP_VERSION}")
        return 0
    try:
        if arguments.check_ui:
            from desktop.ui_check import run_ui_check

            # WebView2 may finish profile writes briefly after its window is destroyed.
            # Test runner can clean a remaining temporary profile after process exit.
            with tempfile.TemporaryDirectory(
                prefix="keshi-ui-check-", ignore_cleanup_errors=True
            ) as directory:
                result = run_ui_check(Path(directory), arguments.check_ui)
            print(json.dumps(result, ensure_ascii=True))
            return 0
        if arguments.smoke_test:
            return _run_smoke_test(arguments.data_dir)
        mode = "browser" if arguments.browser else "desktop" if arguments.desktop else "auto"
        return _run_gui(arguments.data_dir, mode=mode)
    except DesktopRuntimeError as exc:
        LOGGER.error("启动失败：%s", exc)
        if arguments.smoke_test or arguments.check_ui:
            print(
                json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=True),
                file=sys.stderr,
            )
        else:
            _show_native_message(f"{APP_NAME}无法启动", str(exc), error=True)
        return 2
    except Exception as exc:
        details = "".join(traceback.format_exception(exc))
        LOGGER.critical("未处理异常\n%s", details)
        message = (
            "课石未能完成启动。请确认完整解压安装包；不要关闭系统安全保护。\n"
            "可尝试包内的“使用浏览器启动课石”入口。若仍失败，请提供日志：\n"
            "%LOCALAPPDATA%\\Keshi\\logs\\desktop.log\n\n"
            f"错误：{exc}"
        )
        if arguments.smoke_test or arguments.check_ui:
            print(
                json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=True),
                file=sys.stderr,
            )
        else:
            _show_native_message(f"{APP_NAME}遇到错误", message, error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
