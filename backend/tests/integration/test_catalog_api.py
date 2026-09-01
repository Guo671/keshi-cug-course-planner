from app.infrastructure.tables import CatalogCourse, CatalogSection, CatalogSnapshot
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def test_catalog_reports_data_quality_and_hides_no_uncertainty(
    client: TestClient,
    auth_headers: dict[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        db.add(
            CatalogSnapshot(
                id="latest",
                label="2026-08-23 课程总库",
                source_path="fixture.zip",
                source_sha256="a" * 64,
                source_rank=100,
                is_primary=True,
            )
        )
        db.add(
            CatalogSnapshot(
                id="older",
                label="旧版补充快照",
                source_path="old.xls",
                source_sha256="b" * 64,
                source_rank=10,
                is_primary=False,
            )
        )
        course = CatalogCourse(id="20739000:测试技术", code="20739000", name="测试技术")
        db.add(course)
        db.add(
            CatalogSection(
                id="20739000:0001",
                course_id=course.id,
                section_code="0001",
                display_name="测试技术-0001",
                instructors=["示例教师"],
                meetings=[
                    {
                        "weeks": list(range(1, 17)),
                        "weekday": 2,
                        "start_period": 3,
                        "end_period": 4,
                        "precision": "exact_slot",
                    }
                ],
                source_snapshot_id="older",
                source_rank=10,
                needs_confirmation=True,
                default_eligible=False,
                capacity=None,
                enrolled_count=12,
            )
        )
        db.commit()

    status = client.get("/api/catalog/status", headers=auth_headers)
    assert status.status_code == 200
    assert status.json()["confirmation_required_count"] == 1
    assert status.json()["primary_section_count"] == 0

    search = client.get("/api/catalog/search?q=20739000", headers=auth_headers)
    assert search.status_code == 200
    assert search.json()[0]["confirmation_required_section_count"] == 1

    detail = client.get(
        f"/api/catalog/courses/{search.json()[0]['id']}", headers=auth_headers
    )
    assert detail.status_code == 200
    section = detail.json()["sections"][0]
    assert section["needs_confirmation"] is True
    assert section["default_eligible"] is False
    assert section["capacity"] is None
