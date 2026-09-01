from __future__ import annotations

import pytest
from app.api.schemas import CurriculumSelection
from app.application.curricula import (
    CurriculumError,
    preview_for_profile,
    resolve_required_curriculum_choices,
)
from app.infrastructure.tables import (
    CatalogCourse,
    CatalogSection,
    CatalogSnapshot,
    StudentProfile,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


@pytest.mark.parametrize(
    ("major", "major_code"),
    [("自动化", "080801"), ("测控技术与仪器", "080301")],
)
@pytest.mark.parametrize("semester", range(1, 9))
def test_every_verified_plan_semester_previews_without_server_error(
    session_factory: sessionmaker[Session],
    major: str,
    major_code: str,
    semester: int,
) -> None:
    profile = StudentProfile(
        user_id=999,
        college="人工智能与自动化学院",
        major=major,
        major_code=major_code,
        cohort_year=2024,
        cooperation_program="无",
    )
    with session_factory() as db:
        preview = preview_for_profile(db, profile, semester=semester)
    assert preview.manual_only is False
    assert preview.source is not None
    assert preview.source.supports_import is True


def test_cooperative_identity_cannot_reuse_ordinary_plan(
    session_factory: sessionmaker[Session],
) -> None:
    profile = StudentProfile(
        user_id=999,
        college="人工智能与自动化学院",
        major="自动化",
        major_code="080801",
        cohort_year=2024,
        cooperation_program="中外合作办学",
    )
    with session_factory() as db:
        preview = preview_for_profile(db, profile, semester=5)
    assert preview.manual_only is True
    assert preview.courses == []


@pytest.mark.parametrize(
    ("major", "major_code"),
    [("自动化", "080301"), ("测控技术与仪器", "080801")],
)
def test_conflicting_major_name_and_code_cannot_cross_load_a_plan(
    session_factory: sessionmaker[Session],
    major: str,
    major_code: str,
) -> None:
    profile = StudentProfile(
        user_id=999,
        college="人工智能与自动化学院",
        major=major,
        major_code=major_code,
        cohort_year=2024,
        cooperation_program="无",
    )
    with session_factory() as db:
        preview = preview_for_profile(db, profile, semester=5)
    assert preview.manual_only is True
    assert preview.source is None
    assert "相互矛盾" in " ".join(preview.warnings)


def test_missing_plan_is_explicitly_manual_only(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    saved = client.put(
        "/api/profile",
        headers=auth_headers,
        json={
            "college": "机械与电子信息学院",
            "major": "机械设计制造及其自动化",
            "cohort_year": 2024,
            "cooperation_program": "无",
        },
    )
    assert saved.status_code == 200
    response = client.get("/api/curricula/preview?semester=5", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["manual_only"] is True
    assert response.json()["warnings"]


def test_registry_api_exposes_all_researched_sources(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/curricula/sources", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 97
    assert sum(item["supports_import"] for item in response.json()) == 2


def test_preview_preserves_unknown_time_counts_for_explicit_mixed_mode_confirmation(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        db.add(
            CatalogSnapshot(
                id="latest",
                label="latest",
                source_path="latest.zip",
                source_sha256="a" * 64,
                source_rank=100,
                is_primary=True,
            )
        )
        course = CatalogCourse(
            id="automation-practice",
            code="42313600",
            name="控制理论综合实践",
        )
        db.add(course)
        db.add(
            CatalogSection(
                id="automation-practice-0001",
                course_id=course.id,
                section_code="0001",
                display_name="控制理论综合实践-0001",
                instructors=["实践教师"],
                meetings=[
                    {
                        "weeks": [17],
                        "precision": "week_only",
                        "source_ref": "fixture:A1",
                    }
                ],
                source_snapshot_id="latest",
                source_rank=100,
                needs_confirmation=True,
                default_eligible=False,
            )
        )
        db.commit()
        profile = StudentProfile(
            user_id=999,
            college="人工智能与自动化学院",
            major="自动化",
            major_code="080801",
            cohort_year=2024,
            cooperation_program="无",
        )
        preview = preview_for_profile(db, profile, semester=5)
    record = next(item for item in preview.courses if item.code == "42313600")
    assert record.matched_course_id == course.id
    assert record.section_count == 1
    assert record.eligible_section_count == 0
    assert record.confirmation_required_section_count == 1
    assert record.unknown_time_section_count == 1


def test_pure_mode_refuses_to_choose_an_automation_track_for_the_student(
    session_factory: sessionmaker[Session],
) -> None:
    profile = StudentProfile(
        user_id=999,
        college="人工智能与自动化学院",
        major="自动化",
        major_code="080801",
        cohort_year=2024,
        cooperation_program="无",
    )
    selection = CurriculumSelection(
        source_id="au:080801:ordinary",
        semester=5,
        confirmed_by_user=True,
    )
    with session_factory() as db, pytest.raises(CurriculumError, match="方向课程组"):
        resolve_required_curriculum_choices(db, profile, selection)


def test_pure_mode_schedules_safe_subset_and_reports_every_omission(
    session_factory: sessionmaker[Session],
) -> None:
    profile = StudentProfile(
        user_id=999,
        college="人工智能与自动化学院",
        major="自动化",
        major_code="080801",
        cohort_year=2024,
        cooperation_program="无",
    )
    selection = CurriculumSelection(
        source_id="au:080801:ordinary",
        semester=5,
        confirmed_by_user=True,
    )
    with session_factory() as db:
        db.add(
            CatalogSnapshot(
                id="latest",
                label="latest",
                source_path="latest.zip",
                source_sha256="a" * 64,
                source_rank=100,
                is_primary=True,
            )
        )
        course = CatalogCourse(
            id="safe-required",
            code="12005300",
            name="形势与政策",
        )
        db.add(course)
        db.add(
            CatalogSection(
                id="safe-required-0001",
                course_id=course.id,
                section_code="0001",
                display_name="形势与政策-0001",
                instructors=["教师"],
                meetings=[
                    {
                        "weeks": list(range(1, 17)),
                        "weekday": 1,
                        "start_period": 1,
                        "end_period": 2,
                        "precision": "exact_slot",
                    }
                ],
                source_snapshot_id="latest",
                source_rank=100,
                needs_confirmation=False,
                default_eligible=True,
            )
        )
        db.flush()
        resolution = resolve_required_curriculum_choices(db, profile, selection)

    assert [choice.course_id for choice in resolution.choices] == [course.id]
    warning_text = " ".join(resolution.warnings)
    assert "部分培养方案排课" in warning_text
    assert "方向课程组" in warning_text
    assert "未匹配" in warning_text
