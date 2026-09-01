"""Runtime isolation, database seeding, and embedded backend lifecycle."""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing, suppress
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from desktop import APP_VERSION, PREFERRED_PORT

LOGGER = logging.getLogger("keshi.desktop")
MUTEX_NAME = "Local\\Keshi-CUG-Course-Planner-2026-Fall"
REQUIRED_DATABASE_TABLES = frozenset({"catalog_courses", "catalog_sections"})


class DesktopRuntimeError(RuntimeError):
    """A user-actionable desktop startup error."""


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Immutable bundled resources and mutable per-user state paths."""

    resource_root: Path
    data_root: Path
    database_path: Path
    seed_database_path: Path
    static_dir: Path
    catalog_dir: Path
    curriculum_registry_path: Path
    webview_storage_path: Path
    log_path: Path
    icon_path: Path

    @classmethod
    def discover(cls, data_root: Path | None = None) -> RuntimePaths:
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            resource_root = Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
            seed_path = resource_root / "seed" / "planner.db"
        else:
            resource_root = Path(__file__).resolve().parents[1]
            seed_path = resource_root / "var" / "planner.db"

        selected_root = data_root
        if selected_root is None:
            override = os.environ.get("CUG_PLANNER_DATA_DIR")
            if override:
                selected_root = Path(override)
            else:
                local_app_data = os.environ.get("LOCALAPPDATA")
                if not local_app_data:
                    local_app_data = str(Path.home() / "AppData" / "Local")
                selected_root = Path(local_app_data) / "Keshi"

        selected_root = selected_root.expanduser().resolve()
        return cls(
            resource_root=resource_root,
            data_root=selected_root,
            database_path=selected_root / "data" / "planner.db",
            seed_database_path=seed_path,
            static_dir=resource_root / "frontend",
            catalog_dir=resource_root / "data" / "catalog",
            curriculum_registry_path=(
                resource_root / "data" / "curricula" / "source_registry.json"
            ),
            webview_storage_path=selected_root / "webview",
            log_path=selected_root / "logs" / "desktop.log",
            icon_path=resource_root / "desktop" / "assets" / "app.ico",
        )

    def ensure_state_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.webview_storage_path.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def validate_resources(self) -> None:
        required = (
            self.static_dir / "index.html",
            self.static_dir / "app.js",
            self.static_dir / "styles.css",
            self.curriculum_registry_path,
            self.seed_database_path,
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            joined = "\n".join(f"• {path}" for path in missing)
            raise DesktopRuntimeError(
                "程序文件不完整。请重新解压完整的便携包，不要单独移动 Keshi.exe。\n\n"
                f"缺少：\n{joined}"
            )

    def configure_backend_environment(self) -> None:
        """Point backend globals at immutable resources and per-user storage."""

        os.environ.update(
            {
                "CUG_PLANNER_RESOURCE_ROOT": str(self.resource_root),
                "CUG_PLANNER_DATABASE_URL": (
                    f"sqlite:///{self.database_path.resolve().as_posix()}"
                ),
                "CUG_PLANNER_STATIC_DIR": str(self.static_dir),
                "CUG_PLANNER_CATALOG_DIR": str(self.catalog_dir),
                "CUG_PLANNER_CURRICULUM_REGISTRY_PATH": str(
                    self.curriculum_registry_path
                ),
            }
        )


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def verify_database(path: Path, *, require_catalog_rows: bool = True) -> dict[str, int]:
    """Verify SQLite integrity and the minimum catalog contract."""

    if not path.is_file() or path.stat().st_size == 0:
        raise DesktopRuntimeError(f"课程数据库不存在或为空：{path}")
    try:
        with closing(_readonly_connection(path)) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or quick_check[0] != "ok":
                raise DesktopRuntimeError(f"课程数据库完整性检查失败：{path}")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing_tables = REQUIRED_DATABASE_TABLES - tables
            if missing_tables:
                missing = "、".join(sorted(missing_tables))
                raise DesktopRuntimeError(f"课程数据库缺少必要数据表：{missing}")
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in sorted(REQUIRED_DATABASE_TABLES)
            }
    except DesktopRuntimeError:
        raise
    except sqlite3.Error as exc:
        raise DesktopRuntimeError(f"无法读取课程数据库：{path}\n{exc}") from exc
    if require_catalog_rows and any(count < 1 for count in counts.values()):
        raise DesktopRuntimeError("课程数据库中没有可用的课程或教学班数据。")
    return counts


def seed_user_database(paths: RuntimePaths) -> bool:
    """Atomically make the first per-user database; never replace user data."""

    paths.ensure_state_directories()
    if paths.database_path.exists():
        verify_database(paths.database_path)
        return False

    verify_database(paths.seed_database_path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="planner-seed-", suffix=".db.tmp", dir=paths.database_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        source = _readonly_connection(paths.seed_database_path)
        destination = sqlite3.connect(temporary_path)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        # Windows' ``_commit`` (used by fsync) requires a writable descriptor.
        with temporary_path.open("r+b") as copied_file:
            os.fsync(copied_file.fileno())
        verify_database(temporary_path)
        os.replace(temporary_path, paths.database_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return True


@dataclass(slots=True)
class ReservedSocket:
    socket: socket.socket
    port: int
    used_preferred_port: bool

    def close(self) -> None:
        with suppress(OSError):
            self.socket.close()


def _bind_local_socket(port: int) -> socket.socket:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("127.0.0.1", port))
        server_socket.listen(128)
        server_socket.set_inheritable(False)
        return server_socket
    except Exception:
        server_socket.close()
        raise


def reserve_backend_socket(preferred_port: int = PREFERRED_PORT) -> ReservedSocket:
    """Pre-bind the server port so checking and Uvicorn cannot race."""

    try:
        server_socket = _bind_local_socket(preferred_port)
        return ReservedSocket(server_socket, preferred_port, True)
    except OSError as exc:
        LOGGER.warning("首选端口 %s 不可用，改用临时端口：%s", preferred_port, exc)
        server_socket = _bind_local_socket(0)
        actual_port = int(server_socket.getsockname()[1])
        return ReservedSocket(server_socket, actual_port, False)


class BackendServer:
    """Uvicorn in a managed thread using a socket reserved by the launcher."""

    def __init__(self, reserved_socket: ReservedSocket) -> None:
        self.reserved_socket = reserved_socket
        self.port = reserved_socket.port
        self.url = f"http://127.0.0.1:{self.port}"
        self._server: Any | None = None
        self._thread: threading.Thread | None = None
        self._failure: BaseException | None = None

    def _serve(self) -> None:
        try:
            import uvicorn
            from app.main import create_app

            config = uvicorn.Config(
                create_app(),
                loop="asyncio",
                http="h11",
                ws="none",
                lifespan="on",
                log_config=None,
                access_log=False,
                server_header=False,
            )
            server = uvicorn.Server(config)
            self._server = server
            server.run(sockets=[self.reserved_socket.socket])
        except BaseException as exc:  # thread boundary; re-raised on the launcher thread
            self._failure = exc

    def start(self, timeout_seconds: float = 20.0) -> dict[str, str]:
        self._thread = threading.Thread(target=self._serve, name="keshi-backend", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout_seconds
        health_url = f"{self.url}/api/health"
        while time.monotonic() < deadline:
            if self._failure is not None:
                raise DesktopRuntimeError(f"本地服务启动失败：{self._failure}") from self._failure
            if self._thread is not None and not self._thread.is_alive():
                raise DesktopRuntimeError("本地服务在启动完成前意外退出。")
            try:
                with urllib.request.urlopen(health_url, timeout=0.75) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("status") == "ok":
                    return {str(key): str(value) for key, value in payload.items()}
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                time.sleep(0.08)
        raise DesktopRuntimeError("本地服务启动超时。请查看日志或重新启动课石。")

    def stop(self, timeout_seconds: float = 10.0) -> None:
        server = self._server
        if server is not None:
            server.should_exit = True
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout_seconds)
        if thread is not None and thread.is_alive() and server is not None:
            LOGGER.warning("本地服务未及时退出，执行强制停止。")
            server.force_exit = True
            thread.join(2.0)
        self.reserved_socket.close()
        try:
            from app.infrastructure.database import engine

            engine.dispose()
        except Exception:
            LOGGER.debug("数据库连接池清理失败", exc_info=True)

    def __enter__(self) -> BackendServer:
        self.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.stop()


class SingleInstance:
    """A process-scoped Windows named mutex."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        create_mutex.restype = ctypes.c_void_p
        handle = create_mutex(None, False, self.name)
        if not handle:
            raise DesktopRuntimeError("无法创建单实例锁。")
        self._handle = int(handle)
        return ctypes.get_last_error() != 183

    def release(self) -> None:
        if self._handle is None or sys.platform != "win32":
            return
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_bool
        close_handle(self._handle)
        self._handle = None

    def __enter__(self) -> SingleInstance:
        if not self.acquire():
            self.release()
            raise DesktopRuntimeError("课石已经在运行。请切换到现有窗口。")
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.release()


def webview2_runtime_available() -> bool:
    """Return whether an Edge WebView2 Evergreen runtime is discoverable."""

    if sys.platform != "win32":
        return False
    candidates = []
    for environment_name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(environment_name)
        if base:
            candidates.append(Path(base) / "Microsoft" / "EdgeWebView" / "Application")
    if any(path.is_dir() and any(path.iterdir()) for path in candidates):
        return True
    try:
        import winreg

        client_id = "{F1E7E10E-6B6D-4F1E-92D1-89E74C7FBE2F}"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for prefix in ("Software", "Software\\WOW6432Node"):
                key_path = f"{prefix}\\Microsoft\\EdgeUpdate\\Clients\\{client_id}"
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        version, _ = winreg.QueryValueEx(key, "pv")
                    if version:
                        return True
                except OSError:
                    continue
    except (ImportError, OSError):
        pass
    return False


def smoke_test(paths: RuntimePaths) -> dict[str, object]:
    """Exercise resource, first-run DB, backend health, and clean shutdown."""

    paths.validate_resources()
    created = seed_user_database(paths)
    paths.configure_backend_environment()
    reserved = reserve_backend_socket()
    server = BackendServer(reserved)
    try:
        health = server.start()
        return {
            "status": "ok",
            "version": APP_VERSION,
            "frozen": bool(getattr(sys, "frozen", False)),
            "database_created": created,
            "database_path": str(paths.database_path),
            "database_size": paths.database_path.stat().st_size,
            "port": reserved.port,
            "preferred_port": reserved.used_preferred_port,
            "health": health,
        }
    finally:
        server.stop()
