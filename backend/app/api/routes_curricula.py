"""Official curriculum evidence and preview endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..application.curricula import (
    CurriculumError,
    CurriculumRegistry,
    preview_for_profile,
)
from .dependencies import CurrentUser, Database
from .schemas import CurriculumPreviewResponse, CurriculumSourceResponse

router = APIRouter(prefix="/curricula", tags=["curricula"])


@router.get("/sources", response_model=list[CurriculumSourceResponse])
def list_sources(
    _user: CurrentUser,
    college: str | None = Query(default=None, max_length=128),
    major: str | None = Query(default=None, max_length=128),
) -> list[CurriculumSourceResponse]:
    registry = CurriculumRegistry()
    try:
        sources = registry.sources()
    except CurriculumError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if college:
        sources = [item for item in sources if college in str(item.get("college_name", ""))]
    if major:
        sources = [item for item in sources if major in str(item.get("major", ""))]
    return [
        CurriculumSourceResponse(
            id=str(source.get("id")),
            college=str(source.get("college_name")),
            major=str(source.get("major")),
            cohort_year=None,
            plan_variant=source.get("variant"),
            status=f"{source.get('access_level')} · {source.get('status')}",
            official_url=source.get("landing_url"),
            document_url=source.get("direct_url"),
            checked_at=source.get("retrieved_at"),
            note=source.get("notes") or source.get("evidence"),
            supports_import=source.get("access_level") == "A",
        )
        for source in sources
    ]


@router.get("/preview", response_model=CurriculumPreviewResponse)
def preview(
    db: Database,
    user: CurrentUser,
    semester: int | None = Query(default=None, ge=1, le=16),
) -> CurriculumPreviewResponse:
    if user.profile is None:
        raise HTTPException(status_code=409, detail="请先明确填写学院、专业和入学年级")
    resolved_semester = semester or user.profile.semester_override
    if resolved_semester is None:
        resolved_semester = 2 * (2026 - user.profile.cohort_year) + 1
    try:
        return preview_for_profile(db, user.profile, semester=resolved_semester)
    except CurriculumError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
