"""Shared isolated API test fixtures."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from app.api.dependencies import get_session
from app.infrastructure.database import initialize_database
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def test_engine() -> Engine:
    # StaticPool makes the one in-memory SQLite database visible to every
    # TestClient worker connection.
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    initialize_database(engine)
    return engine


@pytest.fixture
def session_factory(test_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=test_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    app = create_app(serve_frontend=False)

    def override_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"username": "test_student", "password": "correct-horse-123"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
