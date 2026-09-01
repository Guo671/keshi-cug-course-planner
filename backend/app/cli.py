"""Command-line entry points for setup, import and local serving."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from .application.catalog_import import replace_persisted_catalog
from .config import PROJECT_ROOT, settings
from .importers import import_schedule_files, import_schedule_zip, merge_snapshots
from .importers.models import CatalogSnapshot
from .infrastructure.database import create_database_engine, initialize_database, session_scope

_COURSE_EXPORT_RE = re.compile(r"^[^-]+-.+\(\d+\)\.xls$", re.IGNORECASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CUG 2026 秋季本地排课助手")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="创建本地数据库表")
    importer = subparsers.add_parser("import-catalog", help="导入并合并课程总库")
    importer.add_argument("--newest", type=Path, help="最新版课程课表 ZIP")
    importer.add_argument(
        "--legacy-dir",
        type=Path,
        action="append",
        default=[],
        help="旧版课程导出目录（可重复）",
    )
    importer.add_argument(
        "--legacy-file",
        type=Path,
        action="append",
        default=[],
        help="旧版 .xls 文件（可重复）",
    )
    importer.add_argument("--database-url", default=settings.database_url)
    importer.add_argument("--strict", action="store_true")

    server = subparsers.add_parser("serve", help="在本机启动 Web 软件")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", default=8765, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-db":
        initialize_database()
        print(f"数据库已初始化：{settings.database_url}")
        return 0
    if args.command == "import-catalog":
        return _import_catalog(args)
    if args.command == "serve":
        import uvicorn

        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit("默认仅允许绑定本机地址；如需部署，请先配置认证与反向代理")
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)
        return 0
    return 2


def _import_catalog(args: argparse.Namespace) -> int:
    newest_path = (args.newest or _default_newest_path()).resolve()
    if not newest_path.is_file():
        raise SystemExit(f"找不到最新版课程总库：{newest_path}")
    legacy_files = _resolve_legacy_files(args.legacy_dir, args.legacy_file)
    print(f"正在安全读取最新版课程总库：{newest_path}")
    newest = import_schedule_zip(
        newest_path,
        snapshot_id="2026-08-23",
        strict=args.strict,
    )
    legacy_snapshots: tuple[CatalogSnapshot, ...] = ()
    if legacy_files:
        print(f"正在读取 {len(legacy_files)} 个旧版课程导出…")
        legacy_snapshots = (
            import_schedule_files(
                legacy_files,
                snapshot_id="2026-08-12-to-13",
                strict=args.strict,
            ),
        )
    merged = merge_snapshots(newest, *legacy_snapshots, include_old_only=False)
    target_engine = create_database_engine(args.database_url)
    initialize_database(target_engine)
    factory = sessionmaker(bind=target_engine, expire_on_commit=False, autoflush=False)
    with session_scope(factory) as db:
        summary = replace_persisted_catalog(db, merged)
    payload = {
        **as_json_dict(summary),
        "newest_zip": str(newest_path),
        "legacy_files": len(legacy_files),
        "policy": "A:newest-wins,old-only-needs-confirmation",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _default_newest_path() -> Path:
    candidates = [
        PROJECT_ROOT.parent / "课程课表 (1).zip",
        PROJECT_ROOT.parent / "课程课表.zip",
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def _resolve_legacy_files(
    supplied_dirs: list[Path], supplied_files: list[Path]
) -> list[Path]:
    if not supplied_dirs and not supplied_files:
        parent = PROJECT_ROOT.parent
        supplied_dirs = [
            parent,
            parent / "所有体育课大集合",
            parent / "所有线性代数",
            parent / "所有通识选修课大集合",
        ]
    files: list[Path] = []
    for directory in supplied_dirs:
        directory = directory.resolve()
        if not directory.is_dir():
            continue
        files.extend(
            path
            for path in directory.glob("*.xls")
            if _COURSE_EXPORT_RE.fullmatch(path.name)
        )
    files.extend(path.resolve() for path in supplied_files if path.is_file())
    return sorted(set(files), key=lambda path: str(path).casefold())


def as_json_dict(value: object) -> dict[str, object]:
    fields = getattr(value, "__dataclass_fields__", {})
    return {name: getattr(value, name) for name in fields}


if __name__ == "__main__":
    raise SystemExit(main())
