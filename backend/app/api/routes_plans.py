"""Schedule generation endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..application.curricula import (
    CurriculumError,
    mixed_curriculum_warnings,
    resolve_required_curriculum_choices,
    validate_curriculum_selection,
)
from ..application.planner import PlanningInputError, generate_schedule
from ..application.planning_storage import (
    CorruptPlanningDataError,
    DraftNotFoundError,
    PlanningRunNotFoundError,
    UnsupportedPlanningSchemaError,
    list_planning_runs,
    read_planning_draft,
    read_planning_run,
    save_planning_draft,
)
from .dependencies import CurrentUser, Database
from .schemas import (
    InputMode,
    PlanningDraft,
    PlanningDraftResponse,
    PlanningRunDetail,
    PlanningRunSummary,
    PlanRequest,
    PlanResponse,
)

router = APIRouter(prefix="/plans", tags=["planning"])


@router.get("/draft", response_model=PlanningDraftResponse)
def get_draft(db: Database, user: CurrentUser) -> PlanningDraftResponse:
    try:
        return read_planning_draft(db, user)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (UnsupportedPlanningSchemaError, CorruptPlanningDataError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/draft", response_model=PlanningDraftResponse)
def put_draft(
    payload: PlanningDraft,
    db: Database,
    user: CurrentUser,
) -> PlanningDraftResponse:
    return save_planning_draft(db, user, payload)


@router.get("/history", response_model=list[PlanningRunSummary])
def history(
    db: Database,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[PlanningRunSummary]:
    return list_planning_runs(db, user, limit=limit)


@router.get("/history/{run_id}", response_model=PlanningRunDetail)
def history_detail(
    run_id: str,
    db: Database,
    user: CurrentUser,
) -> PlanningRunDetail:
    try:
        return read_planning_run(db, user, run_id)
    except PlanningRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CorruptPlanningDataError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/generate", response_model=PlanResponse)
def generate(payload: PlanRequest, db: Database, user: CurrentUser) -> PlanResponse:
    if user.profile is None:
        raise HTTPException(status_code=409, detail="请先明确填写学院、专业和入学年级")
    try:
        resolved_choices = None
        curriculum_warnings: list[str] = []
        if payload.input_mode is InputMode.CURRICULUM:
            if payload.curriculum is None:
                raise CurriculumError("培养方案模式缺少来源与学期确认")
            resolution = resolve_required_curriculum_choices(db, user.profile, payload.curriculum)
            resolved_choices = list(resolution.choices)
            curriculum_warnings = list(resolution.warnings)
        elif payload.input_mode is InputMode.MIXED:
            if payload.curriculum is None:
                raise CurriculumError("混合方式缺少培养方案来源")
            preview = validate_curriculum_selection(db, user.profile, payload.curriculum)
            curriculum_warnings = mixed_curriculum_warnings(preview, payload.manual_courses)
        response = generate_schedule(
            db,
            user,
            payload,
            resolved_choices=resolved_choices,
            additional_warnings=curriculum_warnings,
        )
    except (CurriculumError, PlanningInputError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PlanResponse.model_validate(response)
