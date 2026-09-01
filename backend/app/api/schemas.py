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
    college: NonEmptyText
    major: NonEmptyText
    major_code: str | None = Field(default=None, max_length=32)
    cohort_year: int = Field(ge=2015, le=2026)
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
    CURRICULUM = "curriculum"
    MIXED = "mixed"


class CourseChoice(BaseModel):
    course_id: str = Field(min_length=1, max_length=160)
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
        if any(week < 1 or week > 21 for week in self.weeks):
            raise ValueError("教学周必须在 1–21 周内")
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
    max_solutions: int = Field(default=10, ge=1, le=10)
    phase: Literal["preselection", "confirmation", "add_drop", "retake"] = "confirmation"
    retake_eligibility_confirmed: bool = False


class CurriculumSelection(BaseModel):
    source_id: str | None = None
    semester: int | None = Field(default=None, ge=1, le=16)
    include_optional: bool = False
    confirmed_by_user: bool = False


class PlanRequest(BaseModel):
    input_mode: InputMode
    manual_courses: list[CourseChoice] = Field(default_factory=list, max_length=200)
    curriculum: CurriculumSelection | None = None
    preferences: PlanningPreferences = Field(default_factory=PlanningPreferences)

    @model_validator(mode="after")
    def validate_mode_payload(self) -> PlanRequest:
        if self.input_mode is InputMode.MANUAL and not self.manual_courses:
            raise ValueError("手动模式至少需要添加一门课程")
        if self.input_mode in {InputMode.CURRICULUM, InputMode.MIXED} and self.curriculum is None:
            raise ValueError("培养方案或混合模式需要指定培养方案")
        return self


class PlanningDraft(BaseModel):
    """Potentially incomplete planning input saved between local sessions."""

    model_config = ConfigDict(extra="forbid")

    input_mode: InputMode = InputMode.MANUAL
    manual_courses: list[CourseChoice] = Field(default_factory=list, max_length=200)
    curriculum: CurriculumSelection | None = None
    preferences: PlanningPreferences = Field(default_factory=PlanningPreferences)


class PlanningDraftResponse(BaseModel):
    schema_version: int
    draft: PlanningDraft
    updated_at: datetime
    catalog_fingerprint: str
    current_catalog_fingerprint: str
    catalog_is_stale: bool
    stale_reason: str | None = None


class CurriculumCourseResponse(BaseModel):
    code: str
    name: str
    semester: int
    credits: float | None = None
    required: bool = True
    matched_course_id: str | None = None
    match_state: str = "unmatched"
    category: str | None = None
    requirement_type: str | None = None
    selection_group: str | None = None
    selection_rule: str | None = None
    source_page: int | None = None
    section_count: int = 0
    eligible_section_count: int = 0
    confirmation_required_section_count: int = 0
    legacy_only_section_count: int = 0
    data_quality_confirmation_section_count: int = 0
    unknown_time_section_count: int = 0


class CurriculumSourceResponse(BaseModel):
    id: str
    college: str
    major: str
    cohort_year: int | None = None
    plan_variant: str | None = None
    status: str
    official_url: str | None = None
    document_url: str | None = None
    checked_at: str | None = None
    note: str | None = None
    supports_import: bool = False


class CurriculumPreviewResponse(BaseModel):
    source: CurriculumSourceResponse | None
    semester: int
    courses: list[CurriculumCourseResponse]
    manual_only: bool
    warnings: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    status: str
    plans: list[dict[str, Any]]
    plan_limit: int = Field(ge=1, le=10)
    all_plans_returned: bool
    plans_truncated: bool
    diagnostics: list[dict[str, Any]]
    warnings: list[str]
    catalog_fingerprint: str
    phase: str

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
