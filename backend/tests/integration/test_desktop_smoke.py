from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

from app import __version__
from app.config import PROJECT_ROOT
from app.main import create_app

from desktop import APP_VERSION


def _desktop_environment() -> dict[str, str]:
    environment = os.environ.copy()
    python_paths = [str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")]
    existing = environment.get("PYTHONPATH")
    if existing:
        python_paths.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(python_paths)
    for key in tuple(environment):
        if key.startswith("CUG_PLANNER_"):
            environment.pop(key)
    return environment


def _run_desktop(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "desktop", *arguments],
        cwd=PROJECT_ROOT,
        env=_desktop_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )


def test_versions_stay_in_lockstep() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as project_file:
        project_version = tomllib.load(project_file)["project"]["version"]

    assert project_version == __version__ == APP_VERSION == "0.2.0"
    assert create_app(serve_frontend=False).version == __version__
    version_resource = (PROJECT_ROOT / "desktop" / "windows_version_info.txt").read_text(
        encoding="utf-8"
    )
    assert "StringStruct(u'ProductVersion', u'0.2.0')" in version_resource


def test_desktop_version_command_is_scriptable() -> None:
    completed = _run_desktop("--version")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "Keshi 0.2.0"


def test_desktop_smoke_copies_once_preserves_state_and_releases_port(tmp_path: Path) -> None:
    data_root = tmp_path / "desktop-state"
    first = _run_desktop("--smoke-test", "--data-dir", str(data_root))
    assert first.returncode == 0, first.stderr
    first_result = json.loads(first.stdout)
    assert first_result["status"] == "ok"
    assert first_result["frozen"] is False
    assert first_result["database_created"] is True
    assert first_result["health"] == {"status": "ok", "term": "2026-fall"}

    database_path = data_root / "data" / "planner.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE desktop_test_sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO desktop_test_sentinel VALUES ('preserved')")

    second = _run_desktop("--smoke-test", "--data-dir", str(data_root))
    assert second.returncode == 0, second.stderr
    second_result = json.loads(second.stdout)
    assert second_result["database_created"] is False
    with sqlite3.connect(database_path) as connection:
        sentinel = connection.execute("SELECT value FROM desktop_test_sentinel").fetchone()
    assert sentinel == ("preserved",)

    released = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        released.bind(("127.0.0.1", int(second_result["port"])))
    finally:
        released.close()
