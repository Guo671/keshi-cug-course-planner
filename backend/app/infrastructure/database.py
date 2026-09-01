"""SQLAlchemy setup and transaction helpers."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or database_url.endswith(":memory:"):
        return
    database_path = Path(database_url.removeprefix(prefix))
    database_path.parent.mkdir(parents=True, exist_ok=True)


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or settings.database_url
    _ensure_sqlite_parent(url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, future=True)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _configure_sqlite)
    return engine


def _configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


engine = create_database_engine()
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def initialize_database(target_engine: Engine | None = None) -> None:
    # Importing tables registers every ORM model on Base.metadata.
    from . import tables as _tables  # noqa: F401

    Base.metadata.create_all(target_engine or engine)


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Generator[Session, None, None]:
    session = (factory or SessionFactory)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session, None, None]:
    with session_scope() as session:
        yield session
