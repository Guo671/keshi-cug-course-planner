"""Per-user planning draft and immutable run-history persistence."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.schemas import (
    PLANNING_SCHEMA_VERSION,
    PlanningDraft,
    PlanningDraftResponse,
    PlanningRunDetail,
    PlanningRunSummary,
)
from ..infrastructure.tables import PlanningRun, SavedPreferences, User, utc_now
from .catalog_state import catalog_fingerprint


class DraftNotFoundError(LookupError):
    pass


class PlanningRunNotFoundError(LookupError):
    pass


class UnsupportedPlanningSchemaError(ValueError):
    pass


class CorruptPlanningDataError(ValueError):
    pass


def save_planning_draft(
    db: Session,
    user: User,
    draft: PlanningDraft,
) -> PlanningDraftResponse:
    current_fingerprint = catalog_fingerprint(db)
    updated_at = utc_now()
    envelope: dict[str, Any] = {
        "schema_version": PLANNING_SCHEMA_VERSION,
        "draft": draft.model_dump(mode="json"),
        "catalog_fingerprint": current_fingerprint,
    }
    row = db.get(SavedPreferences, user.id)
    if row is None:
        row = SavedPreferences(
            user_id=user.id,
            payload=envelope,
            updated_at=updated_at,
        )
        db.add(row)
    else:
        # Assign a fresh JSON object so SQLAlchemy always observes the change.
        row.payload = envelope
        row.updated_at = updated_at
    db.flush()
    return _draft_response(row, current_fingerprint=current_fingerprint)


def read_planning_draft(
    db: Session,
    user: User,
) -> PlanningDraftResponse:
    row = db.get(SavedPreferences, user.id)
    if row is None:
        raise DraftNotFoundError("尚未保存排课草稿")
    return _draft_response(row, current_fingerprint=catalog_fingerprint(db))


def list_planning_runs(
    db: Session,
    user: User,
    *,
    limit: int,
) -> list[PlanningRunSummary]:
    if not 1 <= limit <= 100:
        raise ValueError("history limit must be between 1 and 100")
    current_fingerprint = catalog_fingerprint(db)
    rows = list(
        db.scalars(
            select(PlanningRun)
            .where(PlanningRun.user_id == user.id)
            .order_by(PlanningRun.created_at.desc(), PlanningRun.id.desc())
            .limit(limit)
        )
    )
    return [_run_summary(row, current_fingerprint=current_fingerprint) for row in rows]


def read_planning_run(
    db: Session,
    user: User,
    run_id: str,
) -> PlanningRunDetail:
    row = db.scalar(
        select(PlanningRun).where(
            PlanningRun.id == run_id,
            PlanningRun.user_id == user.id,
        )
    )
    if row is None:
        # Do not reveal whether another local user owns this identifier.
        raise PlanningRunNotFoundError("未找到该历史方案")
    if not isinstance(row.request_json, dict) or not isinstance(row.result_json, dict):
        raise CorruptPlanningDataError("历史方案内容损坏，无法安全读取")
    summary = _run_summary(row, current_fingerprint=catalog_fingerprint(db))
    return PlanningRunDetail(
        **summary.model_dump(),
        request=dict(row.request_json),
        result=dict(row.result_json),
    )


def _draft_response(
    row: SavedPreferences,
    *,
    current_fingerprint: str,
) -> PlanningDraftResponse:
    envelope = row.payload
    if not isinstance(envelope, dict):
        raise CorruptPlanningDataError("已保存草稿不是有效对象")
    raw_version = envelope.get("schema_version")
    if raw_version != PLANNING_SCHEMA_VERSION:
        raise UnsupportedPlanningSchemaError(
            f"草稿格式版本不受支持：{raw_version!r}；当前版本为 {PLANNING_SCHEMA_VERSION}"
        )
    raw_draft = envelope.get("draft")
    if not isinstance(raw_draft, dict):
        raise CorruptPlanningDataError("已保存草稿缺少 draft 对象")
    stored_fingerprint = envelope.get("catalog_fingerprint")
    if not isinstance(stored_fingerprint, str) or len(stored_fingerprint) != 64:
        raise CorruptPlanningDataError("已保存草稿缺少有效课程总库指纹")
    try:
        draft = PlanningDraft.model_validate(raw_draft)
    except ValidationError as exc:
        raise CorruptPlanningDataError("已保存草稿内容无法通过当前校验") from exc
    is_stale = stored_fingerprint != current_fingerprint
    return PlanningDraftResponse(
        schema_version=PLANNING_SCHEMA_VERSION,
        draft=draft,
        updated_at=row.updated_at,
        catalog_fingerprint=stored_fingerprint,
        current_catalog_fingerprint=current_fingerprint,
        catalog_is_stale=is_stale,
        stale_reason=_stale_reason(is_stale),
    )


def _run_summary(
    row: PlanningRun,
    *,
    current_fingerprint: str,
) -> PlanningRunSummary:
    result = row.result_json if isinstance(row.result_json, dict) else {}
    plans = result.get("plans")
    plan_list = plans if isinstance(plans, list) else []
    primary = plan_list[0] if plan_list and isinstance(plan_list[0], dict) else {}
    scheduled_count = primary.get("scheduled_course_count", 0)
    if not isinstance(scheduled_count, int):
        scheduled_count = 0
    raw_version = result.get("schema_version", PLANNING_SCHEMA_VERSION)
    schema_version = raw_version if isinstance(raw_version, int) else 0
    is_stale = row.catalog_fingerprint != current_fingerprint
    return PlanningRunSummary(
        schema_version=schema_version,
        run_id=row.id,
        input_mode=row.input_mode,
        status=str(result.get("status", "unknown")),
        created_at=row.created_at,
        catalog_fingerprint=row.catalog_fingerprint,
        catalog_is_stale=is_stale,
        stale_reason=_stale_reason(is_stale),
        plan_count=len(plan_list),
        scheduled_course_count=scheduled_count,
    )


def _stale_reason(is_stale: bool) -> str | None:
    if not is_stale:
        return None
    return "课程总库已更新；历史方案中的教学班、时间或可用状态可能已变化，请重新排课"
