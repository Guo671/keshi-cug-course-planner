"""Create a consistent, privacy-checked SQLite seed for desktop packaging."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

PRIVATE_TABLES = (
    "users",
    "login_sessions",
    "student_profiles",
    "saved_preferences",
    "planning_runs",
)
CATALOG_TABLES = ("catalog_courses", "catalog_sections")


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=20)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def prepare_seed(source_path: Path, output_path: Path) -> dict[str, object]:
    if not source_path.is_file():
        raise RuntimeError(f"seed source does not exist: {source_path}")

    with closing(_open_readonly(source_path)) as source:
        tables = _table_names(source)
        missing_catalog = set(CATALOG_TABLES) - tables
        if missing_catalog:
            raise RuntimeError(f"seed source is missing tables: {sorted(missing_catalog)}")
        private_counts = {
            table: _count(source, table) if table in tables else 0 for table in PRIVATE_TABLES
        }
        populated_private = {table: count for table, count in private_counts.items() if count}
        if populated_private:
            raise RuntimeError(
                "refusing to package private user data: "
                + json.dumps(populated_private, ensure_ascii=False, sort_keys=True)
            )
        catalog_counts = {table: _count(source, table) for table in CATALOG_TABLES}
        if any(count < 1 for count in catalog_counts.values()):
            raise RuntimeError(f"seed source has empty catalog tables: {catalog_counts}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="keshi-seed-", suffix=".db.tmp", dir=output_path.parent
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            destination = sqlite3.connect(temporary_path)
            try:
                source.backup(destination)
                destination.execute("PRAGMA journal_mode=DELETE")
                destination.execute("VACUUM")
                destination.commit()
            finally:
                destination.close()

            with closing(_open_readonly(temporary_path)) as verification:
                integrity = verification.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise RuntimeError("generated seed failed SQLite integrity_check")
                copied_counts = {table: _count(verification, table) for table in CATALOG_TABLES}
                copied_private = {
                    table: _count(verification, table)
                    for table in PRIVATE_TABLES
                    if table in _table_names(verification)
                }
            if copied_counts != catalog_counts or any(copied_private.values()):
                raise RuntimeError("generated seed did not preserve the sanitized source")
            os.replace(temporary_path, output_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    return {
        "status": "ok",
        "source": str(source_path.resolve()),
        "output": str(output_path.resolve()),
        "size": output_path.stat().st_size,
        "catalog_counts": catalog_counts,
        "private_counts": private_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    result = prepare_seed(arguments.source, arguments.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
