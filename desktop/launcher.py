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
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")
    )
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


def _run_gui(data_dir: Path | None) -> int:
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
        if not webview2_runtime_available():
            raise DesktopRuntimeError(
                "未检测到 Microsoft Edge WebView2 Runtime。\n\n"
                "请安装 WebView2 Evergreen Runtime 后重新打开课石；Windows 10/11 通常已预装。"
            )
        created = seed_user_database(paths)
        LOGGER.info("用户数据库就绪；首次创建=%s；路径=%s", created, paths.database_path)
        paths.configure_backend_environment()

        reserved = reserve_backend_socket()
        server = BackendServer(reserved)
        try:
            health = server.start()
            LOGGER.info(
                "本地服务已启动；port=%s；preferred=%s；health=%s",
                server.port,
                reserved.used_preferred_port,
                health,
            )

            # Imported only in GUI mode. Diagnostics and CI never initialize a browser.
            import webview

            webview.create_window(
                APP_TITLE,
                server.url,
                width=1240,
                height=760,
                min_size=(960, 640),
                resizable=True,
                text_select=True,
                confirm_close=False,
            )
            webview.start(
                gui="edgechromium",
                debug=False,
                private_mode=False,
                storage_path=str(paths.webview_storage_path),
                icon=str(paths.icon_path),
            )
        finally:
            server.stop()
            LOGGER.info("课石已安全退出")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    arguments = _argument_parser().parse_args(argv)
    if arguments.version:
        print(f"Keshi {APP_VERSION}")
        return 0
    try:
        if arguments.smoke_test:
            return _run_smoke_test(arguments.data_dir)
        return _run_gui(arguments.data_dir)
    except DesktopRuntimeError as exc:
        LOGGER.error("启动失败：%s", exc)
        if arguments.smoke_test:
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
            "课石遇到未预期的错误。请重新启动；若仍失败，请附上日志反馈。\n\n"
            f"错误：{exc}"
        )
        if arguments.smoke_test:
            print(
                json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=True),
                file=sys.stderr,
            )
        else:
            _show_native_message(f"{APP_NAME}遇到错误", message, error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
