from __future__ import annotations

from typing import Any

from app.infrastructure.tables import (
    CatalogCourse,
    CatalogSection,
    CatalogSnapshot,
    PlanningRun,
    SavedPreferences,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def _register_other(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"username": "other_student", "password": "other-strong-password"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _save_profile(client: TestClient, headers: dict[str, str]) -> None:
    response = client.put(
        "/api/profile",
        headers=headers,
        json={"college": "自动化学院", "major": "自动化", "cohort_year": 2024},
    )
    assert response.status_code == 200, response.text


def _seed_catalog(factory: sessionmaker[Session]) -> str:
    course_id = "HISTORY:历史课程"
    with factory() as db:
        db.add(
            CatalogSnapshot(
                id="initial",
                label="initial",
                source_path="initial.zip",
                source_sha256="a" * 64,
                source_rank=100,
                is_primary=True,
            )
        )
        db.add(CatalogCourse(id=course_id, code="HISTORY", name="历史课程", credits=2))
        meeting: dict[str, Any] = {
            "weeks": list(range(1, 17)),
            "weekday": 2,
            "start_period": 3,
            "end_period": 4,
            "precision": "exact_slot",
        }
        db.add(
            CatalogSection(
                id=f"{course_id}:0001",
                course_id=course_id,
                section_code="0001",
                display_name="历史课程-0001",
                instructors=["任课教师"],
                meetings=[meeting],
                source_snapshot_id="initial",
                source_rank=100,
                needs_confirmation=False,
                default_eligible=True,
            )
        )
        db.commit()
    return course_id


def _add_catalog_revision(factory: sessionmaker[Session], revision: str) -> None:
    with factory() as db:
        db.add(
            CatalogSnapshot(
                id=revision,
                label=revision,
                source_path=f"{revision}.zip",
                source_sha256="f" * 64,
                source_rank=200,
                is_primary=False,
            )
        )
        db.commit()


def test_draft_round_trip_is_user_scoped_and_reports_catalog_staleness(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    missing = client.get("/api/plans/draft", headers=auth_headers)
    assert missing.status_code == 404

    draft = {
        "input_mode": "manual",
        "manual_courses": [],
        "preferences": {
            "phase": "preselection",
            "prefer_no_early_class": True,
            "max_solutions": 3,
        },
    }
    saved = client.put("/api/plans/draft", headers=auth_headers, json=draft)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["schema_version"] == 1
    assert body["draft"]["manual_courses"] == []
    assert body["draft"]["preferences"]["prefer_no_early_class"] is True
    assert len(body["catalog_fingerprint"]) == 64
    assert body["catalog_is_stale"] is False
    assert body["updated_at"]

    loaded = client.get("/api/plans/draft", headers=auth_headers)
    assert loaded.status_code == 200
    assert loaded.json()["draft"] == body["draft"]

    other_headers = _register_other(client)
    assert client.get("/api/plans/draft", headers=other_headers).status_code == 404

    _add_catalog_revision(session_factory, "draft-revision")
    stale = client.get("/api/plans/draft", headers=auth_headers)
    assert stale.status_code == 200
    assert stale.json()["catalog_is_stale"] is True
    assert "课程总库已更新" in stale.json()["stale_reason"]
    assert stale.json()["catalog_fingerprint"] != stale.json()["current_catalog_fingerprint"]


def test_unsupported_saved_draft_schema_is_not_silently_loaded(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        user = db.scalar(select(User).where(User.username == "test_student"))
        assert user is not None
        db.add(
            SavedPreferences(
                user_id=user.id,
                payload={
                    "schema_version": 999,
                    "draft": {},
                    "catalog_fingerprint": "a" * 64,
                },
            )
        )
        db.commit()

    response = client.get("/api/plans/draft", headers=auth_headers)
    assert response.status_code == 409
    assert "版本不受支持" in response.json()["detail"]


def test_corrupt_non_object_storage_returns_conflict_instead_of_server_error(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        user = db.scalar(select(User).where(User.username == "test_student"))
        assert user is not None
        db.add(
            SavedPreferences(
                user_id=user.id,
                payload=["corrupt"],  # type: ignore[arg-type]
            )
        )
        db.add(
            PlanningRun(
                id="corrupt-run",
                user_id=user.id,
                input_mode="manual",
                request_json=["corrupt"],  # type: ignore[arg-type]
                result_json=["corrupt"],  # type: ignore[arg-type]
                catalog_fingerprint="a" * 64,
            )
        )
        db.commit()

    draft = client.get("/api/plans/draft", headers=auth_headers)
    assert draft.status_code == 409
    assert "不是有效对象" in draft.json()["detail"]
    history = client.get("/api/plans/history/corrupt-run", headers=auth_headers)
    assert history.status_code == 409
    assert "内容损坏" in history.json()["detail"]


def test_recent_planning_runs_are_immutable_user_scoped_and_stale_aware(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    _save_profile(client, auth_headers)
    course_id = _seed_catalog(session_factory)
    payload = {
        "input_mode": "manual",
        "manual_courses": [
            {
                "course_id": course_id,
                "priority": 100,
                "required": True,
            }
        ],
        "preferences": {"phase": "preselection", "max_solutions": 2},
    }
    generated = client.post("/api/plans/generate", headers=auth_headers, json=payload)
    assert generated.status_code == 200, generated.text
    run_id = generated.json()["run_id"]
    assert generated.json()["schema_version"] == 1
    assert generated.json()["plan_limit"] == 2
    assert generated.json()["all_plans_returned"] is True
    assert generated.json()["plans_truncated"] is False

    history = client.get("/api/plans/history?limit=1", headers=auth_headers)
    assert history.status_code == 200, history.text
    assert len(history.json()) == 1
    summary = history.json()[0]
    assert summary["run_id"] == run_id
    assert summary["status"] == "optimal"
    assert summary["scheduled_course_count"] == 1
    assert summary["catalog_is_stale"] is False

    detail = client.get(f"/api/plans/history/{run_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["schema_version"] == 1
    assert detail.json()["request"]["manual_courses"][0]["course_id"] == course_id
    assert detail.json()["result"]["run_id"] == run_id
    assert detail.json()["result"]["plan_limit"] == 2
    assert detail.json()["result"]["all_plans_returned"] is True
    assert detail.json()["result"]["plans_truncated"] is False

    other_headers = _register_other(client)
    other_list = client.get("/api/plans/history", headers=other_headers)
    assert other_list.status_code == 200
    assert other_list.json() == []
    hidden = client.get(f"/api/plans/history/{run_id}", headers=other_headers)
    assert hidden.status_code == 404

    _add_catalog_revision(session_factory, "history-revision")
    stale = client.get(f"/api/plans/history/{run_id}", headers=auth_headers)
    assert stale.status_code == 200
    assert stale.json()["catalog_is_stale"] is True
    assert "重新排课" in stale.json()["stale_reason"]
