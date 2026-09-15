"""Schedule generation endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete

from ..application.planner import generate_schedule
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
from ..infrastructure.tables import PlanningRun, SavedPreferences
from .dependencies import CurrentUser, Database
from .schemas import (
    PlanningDraft,
    PlanningDraftResponse,
    PlanningRunDetail,
    PlanningRunSummary,
    PlanRequest,
    PlanResponse,
)

router = APIRouter(prefix="/plans", tags=["planning"])


@router.delete("/draft", status_code=204)
def delete_draft(db: Database, user: CurrentUser) -> None:
    db.execute(delete(SavedPreferences).where(SavedPreferences.user_id == user.id))


@router.delete("/history", status_code=204)
def clear_history(db: Database, user: CurrentUser) -> None:
    db.execute(delete(PlanningRun).where(PlanningRun.user_id == user.id))


@router.delete("/history/{run_id}", status_code=204)
def delete_history(run_id: str, db: Database, user: CurrentUser) -> None:
    row = db.get(PlanningRun, run_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="未找到该历史方案，可能已删除")
    db.delete(row)


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
        response = generate_schedule(
            db,
            user,
            payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PlanResponse.model_validate(response)
