from __future__ import annotations

import socket
import sqlite3
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from app.config import Settings

from desktop.runtime import (
    DesktopRuntimeError,
    RuntimePaths,
    SingleInstance,
    reserve_backend_socket,
    seed_user_database,
    verify_database,
)


def _make_catalog_database(path: Path, *, course_code: str = "TEST001") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE catalog_courses (id INTEGER PRIMARY KEY, course_code TEXT NOT NULL);
            CREATE TABLE catalog_sections (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL);
            """
        )
        connection.execute(
            "INSERT INTO catalog_courses (id, course_code) VALUES (1, ?)", (course_code,)
        )
        connection.execute("INSERT INTO catalog_sections (id, course_id) VALUES (1, 1)")


def test_runtime_paths_keep_resources_separate_from_user_state(tmp_path: Path) -> None:
    paths = RuntimePaths.discover(tmp_path / "mutable")

    assert paths.database_path == (tmp_path / "mutable" / "data" / "planner.db").resolve()
    assert paths.webview_storage_path.is_relative_to(paths.data_root)
    assert paths.log_path.is_relative_to(paths.data_root)
    assert not paths.static_dir.is_relative_to(paths.data_root)
    assert paths.static_dir.joinpath("index.html").is_file()


def test_settings_honor_every_frozen_runtime_override(tmp_path: Path) -> None:
    resource_root = tmp_path / "resources"
    database_path = tmp_path / "state" / "planner.db"
    values = {
        "CUG_PLANNER_RESOURCE_ROOT": str(resource_root),
        "CUG_PLANNER_DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
        "CUG_PLANNER_STATIC_DIR": str(resource_root / "site"),
        "CUG_PLANNER_CATALOG_DIR": str(resource_root / "catalog-data"),
        "CUG_PLANNER_CURRICULUM_REGISTRY_PATH": str(resource_root / "plans.json"),
        "CUG_PLANNER_SESSION_HOURS": "24",
    }

    configured = Settings.from_environment(values)

    assert configured.database_url == values["CUG_PLANNER_DATABASE_URL"]
    assert configured.static_dir == resource_root / "site"
    assert configured.catalog_dir == resource_root / "catalog-data"
    assert configured.curriculum_registry_path == resource_root / "plans.json"
    assert configured.session_hours == 24


def test_first_run_seed_is_atomic_and_never_overwrites_user_database(tmp_path: Path) -> None:
    seed_path = tmp_path / "bundled" / "planner.db"
    _make_catalog_database(seed_path)
    paths = replace(
        RuntimePaths.discover(tmp_path / "user"),
        seed_database_path=seed_path,
    )

    assert seed_user_database(paths) is True
    assert verify_database(paths.database_path) == {
        "catalog_courses": 1,
        "catalog_sections": 1,
    }
    with sqlite3.connect(paths.database_path) as connection:
        connection.execute("CREATE TABLE user_sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO user_sentinel VALUES ('keep-me')")
    with sqlite3.connect(seed_path) as connection:
        connection.execute(
            "INSERT INTO catalog_courses (id, course_code) VALUES (2, 'NEW-SEED')"
        )

    assert seed_user_database(paths) is False
    with sqlite3.connect(paths.database_path) as connection:
        sentinel = connection.execute("SELECT value FROM user_sentinel").fetchone()
        course_count = connection.execute("SELECT COUNT(*) FROM catalog_courses").fetchone()[0]
    assert sentinel == ("keep-me",)
    assert course_count == 1


def test_existing_corrupt_user_database_fails_instead_of_being_replaced(tmp_path: Path) -> None:
    seed_path = tmp_path / "bundled" / "planner.db"
    _make_catalog_database(seed_path)
    paths = replace(RuntimePaths.discover(tmp_path / "user"), seed_database_path=seed_path)
    paths.database_path.parent.mkdir(parents=True)
    paths.database_path.write_bytes(b"not sqlite")

    with pytest.raises(DesktopRuntimeError, match="无法读取课程数据库"):
        seed_user_database(paths)
    assert paths.database_path.read_bytes() == b"not sqlite"


def test_reserved_socket_owns_preferred_port_before_server_start() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    preferred_port = int(probe.getsockname()[1])
    probe.close()

    reservation = reserve_backend_socket(preferred_port)
    try:
        assert reservation.used_preferred_port is True
        assert reservation.port == preferred_port
        assert reservation.socket.getsockname()[1] == preferred_port
    finally:
        reservation.close()


def test_reserved_socket_falls_back_when_preferred_port_is_occupied() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    preferred_port = int(occupied.getsockname()[1])
    reservation = reserve_backend_socket(preferred_port)
    try:
        assert reservation.used_preferred_port is False
        assert reservation.port != preferred_port
        assert reservation.port > 0
    finally:
        reservation.close()
        occupied.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex")
def test_windows_named_mutex_rejects_second_instance() -> None:
    mutex_name = f"Local\\Keshi-Test-{uuid.uuid4()}"
    first = SingleInstance(mutex_name)
    second = SingleInstance(mutex_name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        second.release()
        first.release()
