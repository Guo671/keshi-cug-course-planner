"""Application configuration with safe local-first defaults."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings.

    The planner is intentionally local-first.  It never needs or stores a CUG
    unified-authentication password.  Environment variables are provided for
    tests and advanced deployments, while the default database remains inside
    the project directory.
    """

    database_url: str
    session_hours: int = 72
    curriculum_registry_path: Path = PROJECT_ROOT / "data" / "curricula" / "source_registry.json"
    static_dir: Path = PROJECT_ROOT / "frontend"
    catalog_dir: Path = PROJECT_ROOT / "data" / "catalog"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from *environ*, including frozen-desktop path overrides."""

        values = os.environ if environ is None else environ
        resource_root = Path(values.get("CUG_PLANNER_RESOURCE_ROOT", str(PROJECT_ROOT)))
        database_url = values.get(
            "CUG_PLANNER_DATABASE_URL",
            f"sqlite:///{(PROJECT_ROOT / 'var' / 'planner.db').as_posix()}",
        )
        session_hours = int(values.get("CUG_PLANNER_SESSION_HOURS", "72"))
        if session_hours < 1:
            raise ValueError("CUG_PLANNER_SESSION_HOURS must be positive")
        return cls(
            database_url=database_url,
            session_hours=session_hours,
            curriculum_registry_path=Path(
                values.get(
                    "CUG_PLANNER_CURRICULUM_REGISTRY_PATH",
                    str(resource_root / "data" / "curricula" / "source_registry.json"),
                )
            ),
            static_dir=Path(
                values.get("CUG_PLANNER_STATIC_DIR", str(resource_root / "frontend"))
            ),
            catalog_dir=Path(
                values.get("CUG_PLANNER_CATALOG_DIR", str(resource_root / "data" / "catalog"))
            ),
        )


settings = Settings.from_environment()
