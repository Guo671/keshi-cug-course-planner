"""Course-catalog search and provenance endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from rapidfuzz.fuzz import WRatio
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import selectinload

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


@router.get("/status", response_model=CatalogStatusResponse)
def catalog_status(db: Database, _user: CurrentUser) -> CatalogStatusResponse:
    course_count = db.scalar(select(func.count()).select_from(CatalogCourse)) or 0
    section_count = db.scalar(select(func.count()).select_from(CatalogSection)) or 0
    primary_count = (
        db.scalar(
            select(func.count()).select_from(CatalogSection).where(CatalogSection.default_eligible)
        )
        or 0
    )
    confirmation_count = (
        db.scalar(
            select(func.count())
            .select_from(CatalogSection)
            .where(CatalogSection.needs_confirmation)
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
        }
        for snapshot in db.scalars(
            select(CatalogSnapshot).order_by(CatalogSnapshot.source_rank.desc())
        )
    ]
    return CatalogStatusResponse(
        ready=course_count > 0 and section_count > 0,
        course_count=course_count,
        section_count=section_count,
        primary_section_count=primary_count,
        confirmation_required_count=confirmation_count,
        snapshots=snapshots,
        warning=(
            None
            if course_count > 0 and section_count > 0
            else "课程总库尚未导入，请先运行课程数据导入命令"
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
    escaped = term.replace("%", "\\%").replace("_", "\\_")
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
            meeting.get("precision") in unknown_precisions
            for meeting in section.meetings
        )
        for section in sections
    }
    is_legacy_only = {
        section.id: any(
            issue.get("code") == "old_snapshot_only"
            for issue in section.import_issues
        )
        for section in sections
    }
    return CourseSearchResult(
        id=course.id,
        code=course.code,
        name=course.name,
        credits=course.credits,
        section_count=len(sections),
        eligible_section_count=sum(section.default_eligible for section in sections),
        confirmation_required_section_count=sum(section.needs_confirmation for section in sections),
        legacy_only_section_count=sum(is_legacy_only.values()),
        data_quality_confirmation_section_count=sum(
            section.needs_confirmation
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
        needs_confirmation=section.needs_confirmation,
        default_eligible=section.default_eligible,
        parse_confidence=section.parse_confidence,
        source_snapshot_id=section.source_snapshot_id,
        issues=list(section.import_issues),
    )
