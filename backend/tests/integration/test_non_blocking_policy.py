from __future__ import annotations

from io import BytesIO

from app.infrastructure.tables import CatalogCourse, CatalogSection, CatalogSnapshot
from openpyxl import Workbook


def profile(client, h, school="另一所大学"):
    r = client.put(
        "/api/profile",
        headers=h,
        json={"school": school, "college": "工程学院", "major": "测试专业", "cohort_year": 2024},
    )
    assert r.status_code == 200, r.text


def choice(cid, non_blocking=False):
    return {
        "course_id": "custom:" + cid,
        "custom": {
            "name": cid,
            "sections": [
                {
                    "id": "s",
                    "meetings": [
                        {"weeks": [1, 2], "non_blocking": True}
                        if non_blocking
                        else {"weeks": [1, 2], "weekday": 1, "start_period": 1, "end_period": 2}
                    ],
                }
            ],
        },
    }


def test_untimed_practice_does_not_conflict_with_courses_or_practice(client, auth_headers):
    profile(client, auth_headers)
    result = client.post(
        "/api/plans/generate",
        headers=auth_headers,
        json={"manual_courses": [choice("理论"), choice("实习", True), choice("课程设计", True)]},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    plan = body["plans"][0]
    assert plan["scheduled_course_count"] == 3
    assert len(plan["meetings"]) == 1
    assert body["adjustment"] is None
    assert sum(bool(c["non_blocking_weeks"]) for c in plan["selected_courses"]) == 2
    assert not any("社会调查" in w for w in body["warnings"])


def test_practice_ignores_blocked_time_but_retains_teacher_filter(client, auth_headers):
    profile(client, auth_headers)
    c = choice("实践", True)
    c["custom"]["sections"][0]["instructors"] = ["张老师"]
    prefs = {
        "blocked_times": [
            {"id": "x", "weeks": [1, 2], "weekday": 1, "start_period": 1, "end_period": 20}
        ]
    }
    first = client.post(
        "/api/plans/generate",
        headers=auth_headers,
        json={"manual_courses": [c], "preferences": prefs},
    )
    assert first.json()["plans"][0]["scheduled_course_count"] == 1
    prefs["instructor_rules"] = [{"id": "t", "instructor": "张老师", "strength": "hard"}]
    second = client.post(
        "/api/plans/generate",
        headers=auth_headers,
        json={"manual_courses": [c], "preferences": prefs},
    )
    assert second.json()["plans"][0]["scheduled_course_count"] == 0


def test_original_week_only_practice_is_available_without_risk_checkbox(
    client, auth_headers, session_factory
):
    profile(client, auth_headers)
    with session_factory() as db:
        db.add(CatalogSnapshot(id="s", label="s", source_path="s", source_sha256="a" * 64))
        db.add(CatalogCourse(id="practice", code="40000000", name="课程设计"))
        db.add(
            CatalogSection(
                id="p",
                course_id="practice",
                section_code="0001",
                display_name="课程设计",
                source_snapshot_id="s",
                default_eligible=False,
                needs_confirmation=True,
                meetings=[{"weeks": [1, 2], "precision": "week_only"}],
                import_issues=[{"code": "practice_time_unknown", "severity": "warning"}],
            )
        )
        db.commit()
    result = client.post(
        "/api/plans/generate",
        headers=auth_headers,
        json={"manual_courses": [{"course_id": "practice"}]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["plans"][0]["scheduled_course_count"] == 1
    detail = client.get("/api/catalog/courses/practice", headers=auth_headers).json()
    assert detail["unknown_time_section_count"] == 0
    assert detail["non_blocking_section_count"] == 1
    assert detail["sections"][0]["needs_confirmation"] is False
    assert detail["sections"][0]["meetings"][0]["precision"] == "non_blocking"
    status = client.get("/api/catalog/status", headers=auth_headers).json()
    assert status["non_blocking_section_count"] == 1 and status["confirmation_required_count"] == 0


def test_standard_template_allows_non_blocking_and_exact_segments_together(client, auth_headers):
    from app.importers.standard_excel import STANDARD_HEADERS

    book = Workbook()
    book.active.append(STANDARD_HEADERS)
    book.active.append(
        ["ANY001", "其他学校课程", "0001", "", "1-2", "周一", 1, 2, "", "", "精确时间"]
    )
    book.active.append(
        ["ANY001", "其他学校课程", "0001", "", "3-4", "", "", "", "", "", "不占时段"]
    )
    content = BytesIO()
    book.save(content)
    response = client.post(
        "/api/catalog/import-courses",
        headers=auth_headers,
        files=[("files", ("school.xlsx", content.getvalue()))],
    )
    assert response.status_code == 200, response.text
    meetings = response.json()["courses"][0]["custom"]["sections"][0]["meetings"]
    assert len(meetings) == 2 and sum(m["non_blocking"] for m in meetings) == 1
    from zipfile import ZipFile

    archive = BytesIO()
    with ZipFile(archive, "w") as z:
        z.writestr("school.xlsx", content.getvalue())
    imported = client.post(
        "/api/catalog/import",
        headers=auth_headers,
        files=[("files", ("school.zip", archive.getvalue()))],
    )
    assert imported.status_code == 200, imported.text
    status = client.get("/api/catalog/status", headers=auth_headers).json()
    assert status["non_blocking_section_count"] == 1
    assert status["mixed_time_section_count"] == 1
    assert status["primary_section_count"] == 1


def test_known_school_specific_notice_is_not_applied_to_every_school(client, auth_headers):
    profile(client, auth_headers, "中国地质大学（武汉）")
    yes = client.post(
        "/api/plans/generate", headers=auth_headers, json={"manual_courses": [choice("a")]}
    ).json()
    assert any("社会调查" in w for w in yes["warnings"])
    profile(client, auth_headers, "另一所大学")
    no = client.post(
        "/api/plans/generate", headers=auth_headers, json={"manual_courses": [choice("a")]}
    ).json()
    assert not any("社会调查" in w for w in no["warnings"])
