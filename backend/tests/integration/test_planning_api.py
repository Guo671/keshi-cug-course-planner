from __future__ import annotations

from typing import Any

from app.infrastructure.tables import CatalogCourse, CatalogSection, CatalogSnapshot
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def _save_profile(client: TestClient, headers: dict[str, str]) -> None:
    response = client.put(
        "/api/profile",
        headers=headers,
        json={"college": "自动化学院", "major": "自动化", "cohort_year": 2024},
    )
    assert response.status_code == 200, response.text


def _seed_course(
    factory: sessionmaker[Session],
    *,
    course_id: str,
    code: str,
    name: str,
    needs_confirmation: bool = False,
    precision: str = "exact_slot",
    section_count: int = 1,
) -> None:
    with factory() as db:
        snapshot_id = "old" if needs_confirmation else "latest"
        if db.get(CatalogSnapshot, snapshot_id) is None:
            db.add(
                CatalogSnapshot(
                    id=snapshot_id,
                    label=snapshot_id,
                    source_path=f"{snapshot_id}.zip",
                    source_sha256=("b" if needs_confirmation else "a") * 64,
                    source_rank=10 if needs_confirmation else 100,
                    is_primary=not needs_confirmation,
                )
            )
        db.add(CatalogCourse(id=course_id, code=code, name=name))
        meeting: dict[str, Any] = {
            "weeks": list(range(1, 17)),
            "precision": precision,
            "source_ref": "fixture:B2",
        }
        if precision == "exact_slot":
            meeting.update(weekday=1, start_period=1, end_period=2, room="教一楼101")
        for index in range(1, section_count + 1):
            section_code = f"{index:04d}"
            db.add(
                CatalogSection(
                    id=f"{course_id}:{section_code}",
                    course_id=course_id,
                    section_code=section_code,
                    display_name=f"{name}-{section_code}",
                    instructors=[f"任课教师{index}"],
                    meetings=[meeting],
                    source_snapshot_id=snapshot_id,
                    source_rank=10 if needs_confirmation else 100,
                    needs_confirmation=needs_confirmation,
                    default_eligible=not needs_confirmation,
                    enrolled_count=25,
                    capacity=None,
                    import_issues=(
                        [
                            {
                                "code": "old_snapshot_only",
                                "message": "old only",
                                "severity": "warning",
                            }
                        ]
                        if needs_confirmation
                        else []
                    ),
                )
            )
        db.commit()


def _plan_payload(course_id: str, **choice_overrides: Any) -> dict[str, Any]:
    choice = {
        "course_id": course_id,
        "priority": 100,
        "required": True,
        "allow_confirmation_required": False,
        "allow_unknown_time": False,
    }
    choice.update(choice_overrides)
    return {
        "input_mode": "manual",
        "manual_courses": [choice],
        "preferences": {"max_solutions": 2, "phase": "preselection"},
    }


def test_plan_is_generated_and_presents_capacity_as_unknown(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    _save_profile(client, auth_headers)
    _seed_course(
        session_factory,
        course_id="MATH-A1:高等数学A1",
        code="MATH-A1",
        name="高等数学A1",
    )
    response = client.post(
        "/api/plans/generate",
        headers=auth_headers,
        json=_plan_payload("MATH-A1:高等数学A1"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "optimal"
    assert body["plans"][0]["scheduled_course_count"] == 1
    assert body["plans"][0]["meetings"][0]["weekday"] == 1
    assert body["plan_limit"] == 2
    assert body["all_plans_returned"] is True
    assert body["plans_truncated"] is False
    assert any("容量" in warning for warning in body["warnings"])
    assert any("社会调查" in warning for warning in body["warnings"])
    assert len(body["catalog_fingerprint"]) == 64


def test_api_defaults_to_ten_and_reports_confirmed_truncation(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    _save_profile(client, auth_headers)
    course_id = "MANY:多教学班课程"
    _seed_course(
        session_factory,
        course_id=course_id,
        code="MANY",
        name="多教学班课程",
        section_count=11,
    )
    payload = _plan_payload(course_id)
    payload["preferences"].pop("max_solutions")

    response = client.post("/api/plans/generate", headers=auth_headers, json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["plans"]) == 10
    assert body["plan_limit"] == 10
    assert body["all_plans_returned"] is False
    assert body["plans_truncated"] is True
    rankings = [
        (-plan["coverage_score"], plan["soft_penalty"], plan["selected_option_ids"])
        for plan in body["plans"]
    ]
    assert rankings == sorted(rankings)


def test_old_only_course_requires_explicit_opt_in(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    _save_profile(client, auth_headers)
    _seed_course(
        session_factory,
        course_id="20739000:测试技术",
        code="20739000",
        name="测试技术",
        needs_confirmation=True,
    )
    rejected = client.post(
        "/api/plans/generate",
        headers=auth_headers,
        json=_plan_payload("20739000:测试技术"),
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "infeasible"
    assert any("确认" in item["message"] for item in rejected.json()["diagnostics"])

    allowed = client.post(
        "/api/plans/generate",
        headers=auth_headers,
        json=_plan_payload("20739000:测试技术", allow_confirmation_required=True),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["plans"][0]["scheduled_course_count"] == 1
    assert any("旧版快照" in item for item in allowed.json()["plans"][0]["warnings"])


def test_week_only_course_requires_a_separate_time_confirmation(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    _save_profile(client, auth_headers)
    _seed_course(
        session_factory,
        course_id="PRACTICE:生产实习",
        code="PRACTICE",
        name="生产实习",
        precision="week_only",
    )
    rejected = client.post(
        "/api/plans/generate",
        headers=auth_headers,
        json=_plan_payload("PRACTICE:生产实习"),
    )
    assert rejected.json()["status"] == "infeasible"

    allowed = client.post(
        "/api/plans/generate",
        headers=auth_headers,
        json=_plan_payload("PRACTICE:生产实习", allow_unknown_time=True),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["plans"][0]["scheduled_course_count"] == 1
    assert allowed.json()["plans"][0]["meetings"] == []
    assert any("未证明" in item for item in allowed.json()["plans"][0]["warnings"])


def test_old_alternative_cannot_leak_from_a_course_that_also_has_a_current_section(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    _save_profile(client, auth_headers)
    with session_factory() as db:
        db.add_all(
            [
                CatalogSnapshot(
                    id="latest",
                    label="latest",
                    source_path="latest.zip",
                    source_sha256="a" * 64,
                    source_rank=100,
                    is_primary=True,
                ),
                CatalogSnapshot(
                    id="old",
                    label="old",
                    source_path="old.xls",
                    source_sha256="b" * 64,
                    source_rank=10,
                    is_primary=False,
                ),
            ]
        )
        course = CatalogCourse(id="mixed-snapshot-course", code="MIXED", name="快照混合课程")
        db.add(course)
        db.add_all(
            [
                CatalogSection(
                    id="mixed-current",
                    course_id=course.id,
                    section_code="0001",
                    display_name="快照混合课程-0001",
                    instructors=["当前教师"],
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
                ),
                CatalogSection(
                    id="mixed-old",
                    course_id=course.id,
                    section_code="0002",
                    display_name="快照混合课程-0002",
                    instructors=["旧版教师"],
                    meetings=[
                        {
                            "weeks": list(range(1, 17)),
                            "weekday": 2,
                            "start_period": 1,
                            "end_period": 2,
                            "precision": "exact_slot",
                        }
                    ],
                    source_snapshot_id="old",
                    source_rank=10,
                    needs_confirmation=True,
                    default_eligible=False,
                    import_issues=[
                        {
                            "code": "old_snapshot_only",
                            "message": "old only",
                            "severity": "warning",
                        }
                    ],
                ),
            ]
        )
        db.commit()

    payload = _plan_payload(course.id)
    payload["preferences"]["blocked_times"] = [
        {
            "id": "block-current",
            "weekday": 1,
            "start_period": 1,
            "end_period": 2,
            "weeks": list(range(1, 17)),
            "strength": "hard",
        }
    ]
    rejected = client.post("/api/plans/generate", headers=auth_headers, json=payload)
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "infeasible"

    payload["manual_courses"][0]["allow_confirmation_required"] = True
    allowed = client.post("/api/plans/generate", headers=auth_headers, json=payload)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["plans"][0]["selected_option_ids"] == ["mixed-old"]
    assert any("旧版快照" in item for item in allowed.json()["plans"][0]["warnings"])


def test_retake_phase_requires_eligibility_confirmation(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    _save_profile(client, auth_headers)
    _seed_course(
        session_factory,
        course_id="RETAKE:重修课程",
        code="RETAKE",
        name="重修课程",
    )
    payload = _plan_payload("RETAKE:重修课程")
    payload["preferences"]["phase"] = "retake"
    response = client.post("/api/plans/generate", headers=auth_headers, json=payload)
    assert response.status_code == 422
    assert "重修资格" in response.json()["detail"]

    payload["preferences"]["retake_eligibility_confirmed"] = True
    allowed = client.post("/api/plans/generate", headers=auth_headers, json=payload)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["phase"] == "retake"
    assert any("冲突免听" in item for item in allowed.json()["warnings"])


def test_manual_only_identity_cannot_be_recorded_as_mixed_mode(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
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
    _seed_course(
        session_factory,
        course_id="MANUAL:手工课程",
        code="MANUAL",
        name="手工课程",
    )
    payload = _plan_payload("MANUAL:手工课程")
    payload["input_mode"] = "mixed"
    payload["curriculum"] = {
        "source_id": None,
        "semester": 5,
        "confirmed_by_user": True,
    }
    response = client.post("/api/plans/generate", headers=auth_headers, json=payload)
    assert response.status_code == 422
    assert "只能使用手动输入" in response.json()["detail"]


def test_mixed_mode_keeps_omitted_curriculum_requirements_as_warnings(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    _save_profile(client, auth_headers)
    _seed_course(
        session_factory,
        course_id="UNRELATED:无关课程",
        code="UNRELATED",
        name="无关课程",
    )
    payload = _plan_payload("UNRELATED:无关课程")
    payload["input_mode"] = "mixed"
    payload["curriculum"] = {
        "source_id": "au:080801:ordinary",
        "semester": 5,
        "confirmed_by_user": True,
    }

    response = client.post("/api/plans/generate", headers=auth_headers, json=payload)

    assert response.status_code == 200, response.text
    warnings = " ".join(response.json()["warnings"])
    assert "未匹配" in warnings
    assert "方向" in warnings
    assert "不会把它们视为已完成" in warnings
