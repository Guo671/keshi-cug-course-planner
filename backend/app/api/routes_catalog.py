"""Course-catalog search and provenance endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import Response
from rapidfuzz.fuzz import WRatio
from sqlalchemy import and_, case, delete, exists, func, insert, or_, select
from sqlalchemy.orm import selectinload

from ..application.course_policy import (
    effective_precision,
    has_non_blocking_time,
    needs_confirmation,
)
from ..domain.section_identity import needs_component_review
from ..infrastructure.tables import CatalogCourse, CatalogSection, CatalogSnapshot
from .dependencies import CurrentUser, Database
from .schemas import (
    CatalogStatusResponse,
    CourseDetail,
    CourseSearchResult,
    MeetingResponse,
    SectionResponse,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/template")
def course_template(_user: CurrentUser) -> Response:
    from io import BytesIO

    from openpyxl import Workbook  # type: ignore[import-untyped]
    from openpyxl.styles import Font, PatternFill  # type: ignore[import-untyped]

    from ..importers.standard_excel import STANDARD_HEADERS

    book = Workbook()
    sheet = book.active
    sheet.title = "课程课表"
    sheet.append(STANDARD_HEADERS)
    sheet.append(["示例001", "示例课程（请替换）", "0001", "", "1-5,7-8", "周二", 5, 6, "", ""])
    sheet.append(["示例001", "示例课程（请替换）", "0001", "", "1-3,6-8", "周五", 3, 4, "", ""])
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="2D7666")
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 22
    sheet.column_dimensions["B"].width = 30
    sheet.freeze_panes = "A2"
    output = BytesIO()
    book.save(output)
    return Response(
        output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="keshi-course-template.xlsx"'},
    )


@router.post("/restore-builtin")
def restore_builtin(db: Database, _user: CurrentUser) -> dict[str, int]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from ..config import settings

    root = settings.catalog_dir.parent.parent
    seed = root / "seed" / "planner.db"
    if not seed.is_file():
        seed = root / "var" / "planner.db"
    if not seed.is_file():
        raise HTTPException(status_code=404, detail="没有找到内置课程库，请自行导入课程文件")
    engine = create_engine(f"sqlite:///{seed.as_posix()}")
    try:
        # Read all rows before starting any replacement, including source=destination.
        with Session(engine) as source:
            payload = {
                table: [
                    {col.name: getattr(row, col.name) for col in table.__table__.columns}
                    for row in source.scalars(select(table))
                ]
                for table in (CatalogSnapshot, CatalogCourse, CatalogSection)
            }
        if not payload[CatalogCourse] or not payload[CatalogSection]:
            raise HTTPException(status_code=409, detail="内置库为空，请自行导入课程文件")
        for table in (CatalogSection, CatalogCourse, CatalogSnapshot):
            db.execute(delete(table))
        for table in (CatalogSnapshot, CatalogCourse, CatalogSection):
            db.execute(insert(table), payload[table])
        return {"courses": len(payload[CatalogCourse]), "sections": len(payload[CatalogSection])}
    finally:
        engine.dispose()


@router.post("/import")
def import_catalog(files: list[UploadFile], db: Database, _user: CurrentUser) -> dict[str, Any]:
    from dataclasses import asdict

    from ..application.catalog_import import replace_persisted_catalog
    from ..application.uploads import parse_uploads

    try:
        payload = _read_uploads(files, 5)
        catalog, count = parse_uploads(payload, archives=True)
        summary = replace_persisted_catalog(db, catalog)
        return {
            "file_count": count,
            **asdict(summary),
            "warnings": [issue.message for issue in catalog.issues],
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import-courses")
def import_courses(files: list[UploadFile], _user: CurrentUser) -> dict[str, Any]:
    from ..application.uploads import direct_choices, parse_uploads

    try:
        catalog, count = parse_uploads(_read_uploads(files, 20))
        return {"file_count": count, "courses": direct_choices(catalog)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("", status_code=204)
def delete_catalog(db: Database, _user: CurrentUser) -> None:
    db.execute(delete(CatalogSection))
    db.execute(delete(CatalogCourse))
    db.execute(delete(CatalogSnapshot))


def _read_uploads(files: list[UploadFile], maximum: int) -> list[tuple[str, bytes]]:
    if not 1 <= len(files) <= maximum:
        raise ValueError(f"请选择 1–{maximum} 个文件")
    result = []
    total = 0
    for upload in files:
        data = upload.file.read(32 * 1024 * 1024 + 1)
        total += len(data)
        if len(data) > 32 * 1024 * 1024 or total > 100 * 1024 * 1024:
            raise ValueError("单文件不得超过 32 MB，一批不得超过 100 MB")
        result.append((upload.filename or "", data))
    return result


@router.get("/status", response_model=CatalogStatusResponse)
def catalog_status(db: Database, _user: CurrentUser) -> CatalogStatusResponse:
    issues = func.json_each(CatalogSection.import_issues).table_valued("value").alias("issue")
    meetings = func.json_each(CatalogSection.meetings).table_valued("value").alias("meeting")
    practice = or_(
        exists(
            select(1)
            .select_from(issues)
            .where(func.json_extract(issues.c.value, "$.code") == "practice_time_unknown")
        ),
        exists(
            select(1)
            .select_from(meetings)
            .where(
                or_(
                    func.json_extract(meetings.c.value, "$.precision") == "non_blocking",
                    func.json_extract(meetings.c.value, "$.non_blocking") == 1,
                )
            )
        ),
    )
    exact = exists(
        select(1)
        .select_from(meetings)
        .where(
            and_(
                func.coalesce(func.json_extract(meetings.c.value, "$.precision"), "exact_slot")
                == "exact_slot",
                func.coalesce(func.json_extract(meetings.c.value, "$.non_blocking"), 0) != 1,
            )
        )
    )
    old = exists(
        select(1)
        .select_from(issues)
        .where(func.json_extract(issues.c.value, "$.code") == "old_snapshot_only")
    )
    non_blocking_count = (
        db.scalar(select(func.count()).select_from(CatalogSection).where(and_(practice, ~old))) or 0
    )
    course_count = db.scalar(select(func.count()).select_from(CatalogCourse)) or 0
    section_count = db.scalar(select(func.count()).select_from(CatalogSection)) or 0
    primary_count = (
        db.scalar(
            select(func.count())
            .select_from(CatalogSection)
            .where(or_(CatalogSection.default_eligible, and_(practice, ~old)))
        )
        or 0
    )
    confirmation_count = (
        db.scalar(
            select(func.count())
            .select_from(CatalogSection)
            .where(and_(CatalogSection.needs_confirmation, or_(~practice, old)))
        )
        or 0
    )
    snapshots = [
        {
            "id": snapshot.id,
            "label": snapshot.label,
            "captured_at": snapshot.captured_at.isoformat() if snapshot.captured_at else None,
            "is_primary": snapshot.is_primary,
            "source_sha256": snapshot.source_sha256,
            "scope_label": snapshot.metadata_json.get("scope_label"),
        }
        for snapshot in db.scalars(
            select(CatalogSnapshot).order_by(CatalogSnapshot.source_rank.desc())
        )
    ]
    return CatalogStatusResponse(
        mixed_time_section_count=db.scalar(
            select(func.count()).select_from(CatalogSection).where(and_(practice, ~old, exact))
        )
        or 0,
        non_blocking_section_count=non_blocking_count,
        source_label=next(
            (s["scope_label"] for s in snapshots if s.get("scope_label")),
            "用户导入的总库，请核对所属学校和学期",
        ),
        ready=course_count > 0 and section_count > 0,
        course_count=course_count,
        section_count=section_count,
        primary_section_count=primary_count,
        confirmation_required_count=confirmation_count,
        snapshots=snapshots,
        warning=(
            None
            if course_count > 0 and section_count > 0
            else "课程总库为空，可在总库管理中导入；也可以直接导入课程表或手动加课"
        ),
    )


@router.get("/search", response_model=list[CourseSearchResult])
def search_courses(
    db: Database,
    _user: CurrentUser,
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[CourseSearchResult]:
    term = q.strip()
    if not term:
        raise HTTPException(status_code=422, detail="请输入课程名称或课程号")
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    # Exact and prefix matches are handled by SQL first. A modest broader set
    # is rescored in Python to tolerate punctuation/full-width differences.
    statement = (
        select(CatalogCourse)
        .options(selectinload(CatalogCourse.sections))
        .where(
            or_(
                CatalogCourse.code.ilike(like, escape="\\"),
                CatalogCourse.name.ilike(like, escape="\\"),
            )
        )
        .order_by(
            case((CatalogCourse.code == term, 0), else_=1),
            CatalogCourse.code,
            CatalogCourse.name,
        )
        .limit(limit * 3)
    )
    courses = list(db.scalars(statement).unique())
    courses.sort(
        key=lambda course: (
            0 if course.code == term else 1,
            -max(WRatio(term, course.code), WRatio(term, course.name)),
            course.code,
        )
    )
    return [_course_summary(course) for course in courses[:limit]]


@router.get("/courses/{course_id}", response_model=CourseDetail)
def get_course(course_id: str, db: Database, _user: CurrentUser) -> CourseDetail:
    course = db.scalar(
        select(CatalogCourse)
        .options(selectinload(CatalogCourse.sections))
        .where(CatalogCourse.id == course_id)
    )
    if course is None:
        raise HTTPException(status_code=404, detail="未找到该课程")
    summary = _course_summary(course)
    sections = sorted(
        course.sections,
        key=lambda section: (
            section.needs_confirmation,
            section.section_code,
            section.id,
        ),
    )
    return CourseDetail(
        **summary.model_dump(),
        sections=[_section_response(section) for section in sections],
    )


def _course_summary(course: CatalogCourse) -> CourseSearchResult:
    sections = course.sections
    unknown_precisions = {"week_only", "date_range", "tbd"}
    has_unknown_time = {
        section.id: any(
            effective_precision(meeting, section.import_issues) in unknown_precisions
            for meeting in section.meetings
        )
        for section in sections
    }
    is_legacy_only = {
        section.id: any(issue.get("code") == "old_snapshot_only" for issue in section.import_issues)
        for section in sections
    }
    return CourseSearchResult(
        non_blocking_section_count=sum(has_non_blocking_time(s) for s in sections),
        component_review_required=needs_component_review(s.section_code for s in sections),
        id=course.id,
        code=course.code,
        name=course.name,
        credits=course.credits,
        section_count=len(sections),
        eligible_section_count=sum(
            section.default_eligible
            or (has_non_blocking_time(section) and not needs_confirmation(section))
            for section in sections
        ),
        confirmation_required_section_count=sum(
            needs_confirmation(section) for section in sections
        ),
        legacy_only_section_count=sum(is_legacy_only.values()),
        data_quality_confirmation_section_count=sum(
            needs_confirmation(section)
            and not is_legacy_only[section.id]
            and not has_unknown_time[section.id]
            for section in sections
        ),
        unknown_time_section_count=sum(has_unknown_time.values()),
    )


def _section_response(section: CatalogSection) -> SectionResponse:
    meetings = []
    for raw in section.meetings:
        permitted = {key: raw.get(key) for key in MeetingResponse.model_fields if key in raw}
        permitted["precision"] = effective_precision(raw, section.import_issues)
        meetings.append(MeetingResponse.model_validate(permitted))
    return SectionResponse(
        id=section.id,
        section_code=section.section_code,
        display_name=section.display_name,
        instructors=list(section.instructors),
        meetings=meetings,
        composition=list(section.composition),
        assessment=section.assessment,
        enrolled_count=section.enrolled_count,
        capacity=section.capacity,
        needs_confirmation=needs_confirmation(section),
        default_eligible=section.default_eligible
        or (has_non_blocking_time(section) and not needs_confirmation(section)),
        parse_confidence=section.parse_confidence,
        source_snapshot_id=section.source_snapshot_id,
        issues=list(section.import_issues),
    )
