"""Mandatory student identity/profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..infrastructure.tables import StudentProfile
from .dependencies import CurrentUser, Database
from .schemas import StudentProfileInput, StudentProfileResponse

router = APIRouter(prefix="/profile", tags=["profile"])


def _semester_for(cohort_year: int, override: int | None) -> tuple[int, int, bool]:
    inferred = 2 * (2026 - cohort_year) + 1
    # Transfers, leave of absence and major changes can shift the actual plan
    # semester, so the UI must display the inference for confirmation.
    actual = override or inferred
    return inferred, actual, override is None


def _to_response(profile: StudentProfile) -> StudentProfileResponse:
    inferred, semester, needs_confirmation = _semester_for(
        profile.cohort_year, profile.semester_override
    )
    preferences = profile.preferences if isinstance(profile.preferences, dict) else {}
    return StudentProfileResponse(
        college=profile.college,
        major=profile.major,
        major_code=profile.major_code,
        cohort_year=profile.cohort_year,
        plan_variant=profile.plan_variant,
        cooperation_program=profile.cooperation_program,
        administrative_class=preferences.get("administrative_class"),
        semester_override=profile.semester_override,
        inferred_semester=inferred,
        semester=semester,
        semester_mapping_needs_confirmation=needs_confirmation,
    )


@router.get("", response_model=StudentProfileResponse)
def get_profile(user: CurrentUser) -> StudentProfileResponse:
    if user.profile is None:
        raise HTTPException(status_code=404, detail="尚未填写学院、专业和年级")
    return _to_response(user.profile)


@router.put("", response_model=StudentProfileResponse)
def put_profile(
    payload: StudentProfileInput,
    db: Database,
    user: CurrentUser,
) -> StudentProfileResponse:
    inferred, semester, _ = _semester_for(payload.cohort_year, payload.semester_override)
    if semester < 1 or semester > 16:
        raise HTTPException(
            status_code=422,
            detail="该年级已超出普通学制，请明确选择当前实际培养方案学期",
        )
    profile = user.profile
    if profile is None:
        profile = StudentProfile(user_id=user.id)
        db.add(profile)
    profile.college = payload.college
    profile.major = payload.major
    profile.major_code = payload.major_code
    profile.cohort_year = payload.cohort_year
    profile.plan_variant = payload.plan_variant
    profile.cooperation_program = payload.cooperation_program
    existing_preferences = (
        profile.preferences if isinstance(profile.preferences, dict) else {}
    )
    profile.preferences = {
        **existing_preferences,
        "administrative_class": payload.administrative_class,
    }
    profile.semester_override = payload.semester_override
    db.flush()
    return _to_response(profile)
