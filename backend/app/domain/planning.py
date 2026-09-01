"""Input and output data structures for schedule planning."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .constraints import UserConstraints
from .models import Course, SectionOption


@dataclass(frozen=True, slots=True)
class CourseRequest:
    course_id: str
    priority: int = 100
    required: bool = False

    def __post_init__(self) -> None:
        if not self.course_id.strip():
            raise ValueError("course request course_id cannot be blank")
        if self.priority < 0:
            raise ValueError("course priority cannot be negative")


@dataclass(frozen=True, slots=True)
class SchedulingProblem:
    courses: tuple[Course, ...]
    requests: tuple[CourseRequest, ...]
    options: tuple[SectionOption, ...]
    constraints: UserConstraints = UserConstraints()

    def __post_init__(self) -> None:
        _require_unique("course", (course.id for course in self.courses))
        _require_unique("course request", (r.course_id for r in self.requests))
        _require_unique("section option", (option.id for option in self.options))

        course_ids = {course.id for course in self.courses}
        request_ids = {request.course_id for request in self.requests}
        unknown_requests = request_ids - course_ids
        if unknown_requests:
            raise ValueError(
                "course requests reference unknown courses: " + ", ".join(sorted(unknown_requests))
            )
        unknown_options = {option.course_id for option in self.options} - course_ids
        if unknown_options:
            raise ValueError(
                "section options reference unknown courses: " + ", ".join(sorted(unknown_options))
            )
        unrequested_options = {option.course_id for option in self.options} - request_ids
        if unrequested_options:
            raise ValueError(
                "section options reference courses that were not requested: "
                + ", ".join(sorted(unrequested_options))
            )


class SolveStatus(StrEnum):
    OPTIMAL = "optimal"
    FEASIBLE_TIMEOUT = "feasible_timeout"
    INFEASIBLE = "infeasible"
    UNKNOWN = "unknown"
    DATA_ERROR = "data_error"


@dataclass(frozen=True, slots=True)
class RejectionReason:
    code: str
    message: str
    related_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str
    related_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CourseExplanation:
    course_id: str
    selected_option_id: str | None
    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    selected_option_ids: tuple[str, ...]
    unscheduled_course_ids: tuple[str, ...]
    coverage_score: int
    soft_penalty: int
    explanations: tuple[CourseExplanation, ...] = ()


@dataclass(frozen=True, slots=True)
class SolveResult:
    status: SolveStatus
    plans: tuple[SchedulePlan, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    rejected_options: tuple[tuple[str, tuple[RejectionReason, ...]], ...] = ()
    plan_limit: int = 10
    all_plans_returned: bool = False
    plans_truncated: bool = False

    def __post_init__(self) -> None:
        if self.plan_limit < 1:
            raise ValueError("plan_limit must be at least 1")
        if len(self.plans) > self.plan_limit:
            raise ValueError("plans cannot exceed plan_limit")
        if self.all_plans_returned and self.plans_truncated:
            raise ValueError("a result cannot be both exhaustive and truncated")
        if self.plans_truncated and len(self.plans) != self.plan_limit:
            raise ValueError("a truncated result must fill plan_limit")


def _require_unique(label: str, values: Iterable[str]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"duplicate {label} ids: " + ", ".join(sorted(duplicates)))
