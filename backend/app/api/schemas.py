"""Pydantic transport schemas for the local web API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

PLANNING_SCHEMA_VERSION = 1

Username = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=64),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


class RegisterRequest(BaseModel):
    username: Username
    password: SecretStr = Field(min_length=10, max_length=256)


class LoginRequest(RegisterRequest):
    pass


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class UserResponse(BaseModel):
    id: int
    username: str
    profile_complete: bool


class StudentProfileInput(BaseModel):
    school: NonEmptyText = "未填写学校"
    college: NonEmptyText
    major: NonEmptyText
    major_code: str | None = Field(default=None, max_length=32)
    cohort_year: int = Field(ge=2015, le=2100)
    target_year: int = Field(default=2026, ge=2020, le=2100)
    target_season: Literal["fall", "spring"] = "fall"
    plan_variant: str | None = Field(default=None, max_length=128)
    cooperation_program: NonEmptyText = "无"
    administrative_class: str | None = Field(default=None, max_length=32)
    semester_override: int | None = Field(default=None, ge=1, le=16)

    @field_validator("major_code", "plan_variant", "administrative_class", mode="before")
    @classmethod
    def blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value


class StudentProfileResponse(StudentProfileInput):
    inferred_semester: int
    semester: int
    semester_mapping_needs_confirmation: bool


class CatalogStatusResponse(BaseModel):
    mixed_time_section_count: int = 0
    source_label: str | None = None
    non_blocking_section_count: int = 0
    ready: bool
    course_count: int
    section_count: int
    primary_section_count: int
    confirmation_required_count: int
    snapshots: list[dict[str, Any]]
    warning: str | None = None


class MeetingResponse(BaseModel):
    weeks: list[int] = Field(default_factory=list)
    weekday: int | None = None
    start_period: int | None = None
    end_period: int | None = None
    campus: str | None = None
    room: str | None = None
    precision: str = "exact_slot"
    source_ref: str | None = None


class SectionResponse(BaseModel):
    id: str
    section_code: str
    display_name: str
    instructors: list[str]
    meetings: list[MeetingResponse]
    composition: list[str]
    assessment: str | None
    enrolled_count: int | None
    capacity: int | None
    needs_confirmation: bool
    default_eligible: bool
    parse_confidence: float
    source_snapshot_id: str
    issues: list[dict[str, Any]] = Field(default_factory=list)


class CourseSearchResult(BaseModel):
    non_blocking_section_count: int = 0
    component_review_required: bool = False
    id: str
    code: str
    name: str
    credits: float | None
    section_count: int
    eligible_section_count: int
    confirmation_required_section_count: int
    legacy_only_section_count: int
    data_quality_confirmation_section_count: int
    unknown_time_section_count: int


class CourseDetail(CourseSearchResult):
    sections: list[SectionResponse]


class InputMode(StrEnum):
    MANUAL = "manual"


class CustomMeeting(BaseModel):
    non_blocking: bool = False
    weeks: list[Annotated[int, Field(strict=True)]] = Field(default_factory=list, max_length=64)
    weekday: int | None = Field(default=None, ge=1, le=7, strict=True)
    start_period: int | None = Field(default=None, ge=1, le=20, strict=True)
    end_period: int | None = Field(default=None, ge=1, le=20, strict=True)
    room: str | None = Field(default=None, max_length=128)
    campus: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_time(self) -> CustomMeeting:
        if self.non_blocking:
            self.weekday = self.start_period = self.end_period = None
        elif (
            not self.weeks
            or self.weekday is None
            or self.start_period is None
            or self.end_period is None
        ):
            raise ValueError("普通课程必须填写周次、星期、开始及结束节次")
        if (
            self.end_period is not None
            and self.start_period is not None
            and self.end_period < self.start_period
        ):
            raise ValueError("结束节次不能早于开始节次")
        if any(w < 1 or w > 64 for w in self.weeks):
            raise ValueError("教学周必须在 1–64 周内")
        self.weeks = sorted(set(self.weeks))
        return self


class CustomSection(BaseModel):
    id: NonEmptyText
    section_code: NonEmptyText = Field(default="自填教学班", max_length=128)
    instructors: list[NonEmptyText] = Field(default_factory=list, max_length=20)
    meetings: list[CustomMeeting] = Field(min_length=1, max_length=100)


class CustomCourse(BaseModel):
    component_relationship_confirmed: bool = True
    name: NonEmptyText
    code: str = Field(default="自填", max_length=64)
    sections: list[CustomSection] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_sections(self) -> CustomCourse:
        if len({s.id for s in self.sections}) != len(self.sections):
            raise ValueError("教学班标识不能重复")
        return self


class CourseChoice(BaseModel):
    course_id: str = Field(min_length=1, max_length=160)
    custom: CustomCourse | None = None
    priority: int = Field(default=100, ge=0, le=10_000)
    required: bool = False
    locked_section_id: str | None = Field(default=None, max_length=256)
    allow_confirmation_required: bool = False
    allow_unknown_time: bool = False


class BlockedTimeInput(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    weekday: int = Field(ge=1, le=7)
    start_period: int = Field(ge=1, le=20)
    end_period: int = Field(ge=1, le=20)
    weeks: list[int] = Field(default_factory=lambda: list(range(1, 22)), min_length=1)
    strength: Literal["hard", "soft"] = "hard"
    penalty: int = Field(default=100, ge=0, le=1_000_000)
    label: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_range(self) -> BlockedTimeInput:
        if self.end_period < self.start_period:
            raise ValueError("结束节次不能早于开始节次")
        if any(week < 1 or week > 64 for week in self.weeks):
            raise ValueError("教学周必须在 1–64 周内")
        if len(set(self.weeks)) != len(self.weeks):
            raise ValueError("教学周不能重复")
        return self


class InstructorRuleInput(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    instructor: NonEmptyText
    strength: Literal["hard", "soft"] = "hard"
    penalty: int = Field(default=100, ge=0, le=1_000_000)
    label: str | None = Field(default=None, max_length=128)


class PlanningPreferences(BaseModel):
    blocked_times: list[BlockedTimeInput] = Field(default_factory=list, max_length=100)
    instructor_rules: list[InstructorRuleInput] = Field(default_factory=list, max_length=100)
    forbidden_section_ids: list[str] = Field(default_factory=list, max_length=500)
    prefer_no_early_class: bool = False
    prefer_no_evening_class: bool = False
    prefer_compact_days: bool = False
    max_solutions: int = Field(default=10, ge=1, le=100)
    phase: Literal["planning", "preselection", "confirmation", "add_drop", "retake"] = "planning"
    retake_eligibility_confirmed: bool = False


class PlanRequest(BaseModel):
    input_mode: InputMode = InputMode.MANUAL
    manual_courses: list[CourseChoice] = Field(default_factory=list, max_length=200)
    preferences: PlanningPreferences = Field(default_factory=PlanningPreferences)

    @model_validator(mode="after")
    def validate_mode_payload(self) -> PlanRequest:
        if not self.manual_courses:
            raise ValueError("至少需要添加一门课程")
        return self


class PlanningDraft(BaseModel):
    """Potentially incomplete planning input saved between local sessions."""

    model_config = ConfigDict(extra="forbid")

    input_mode: InputMode = InputMode.MANUAL
    manual_courses: list[CourseChoice] = Field(default_factory=list, max_length=200)
    preferences: PlanningPreferences = Field(default_factory=PlanningPreferences)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_mode(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            value.pop("curriculum", None)  # Discard the retired field in v0.2 drafts.
            value["input_mode"] = "manual"
        return value


class PlanningDraftResponse(BaseModel):
    schema_version: int
    draft: PlanningDraft
    updated_at: datetime
    catalog_fingerprint: str
    current_catalog_fingerprint: str
    catalog_is_stale: bool
    stale_reason: str | None = None


class PlanResponse(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    status: str
    plans: list[dict[str, Any]]
    plan_limit: int = Field(ge=1, le=100)
    all_plans_returned: bool
    plans_truncated: bool
    diagnostics: list[dict[str, Any]]
    warnings: list[str]
    catalog_fingerprint: str
    phase: str
    adjustment: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_enumeration_metadata(self) -> PlanResponse:
        if len(self.plans) > self.plan_limit:
            raise ValueError("plans cannot exceed plan_limit")
        if self.all_plans_returned and self.plans_truncated:
            raise ValueError("a result cannot be both exhaustive and truncated")
        if self.plans_truncated and len(self.plans) != self.plan_limit:
            raise ValueError("a truncated result must fill plan_limit")
        return self


class PlanningRunSummary(BaseModel):
    schema_version: int
    run_id: str
    input_mode: str
    status: str
    created_at: datetime
    catalog_fingerprint: str
    catalog_is_stale: bool
    stale_reason: str | None = None
    plan_count: int = 0
    scheduled_course_count: int = 0


class PlanningRunDetail(PlanningRunSummary):
    request: dict[str, Any]
    result: dict[str, Any]
