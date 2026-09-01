"""User-specified hard and soft scheduling constraints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import Meeting, TimePrecision
from .week_mask import WeekMask


class ConstraintStrength(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class SelectionPhase(StrEnum):
    PRESELECTION = "preselection"
    CONFIRMATION = "confirmation"
    ADD_DROP = "add_drop"
    RETAKE = "retake"


@dataclass(frozen=True, slots=True)
class BlockedTime:
    id: str
    weekday: int
    start_period: int
    end_period: int
    weeks: WeekMask
    strength: ConstraintStrength = ConstraintStrength.HARD
    penalty: int = 100
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("blocked-time id cannot be blank")
        if not 1 <= self.weekday <= 7:
            raise ValueError("weekday must be between 1 and 7")
        if self.start_period < 1 or self.end_period < self.start_period:
            raise ValueError("invalid blocked-time period range")
        if self.penalty < 0:
            raise ValueError("constraint penalty cannot be negative")

    def as_meeting(self) -> Meeting:
        return Meeting(
            weeks=self.weeks,
            weekday=self.weekday,
            start_period=self.start_period,
            end_period=self.end_period,
            precision=TimePrecision.EXACT_SLOT,
            source_ref=self.id,
        )


@dataclass(frozen=True, slots=True)
class InstructorRule:
    id: str
    instructor: str
    strength: ConstraintStrength = ConstraintStrength.HARD
    penalty: int = 100
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("instructor-rule id cannot be blank")
        if not self.instructor.strip():
            raise ValueError("instructor name cannot be blank")
        if self.penalty < 0:
            raise ValueError("constraint penalty cannot be negative")

    def matches(self, instructor: str) -> bool:
        return _normalize_instructor(self.instructor) == _normalize_instructor(instructor)


@dataclass(frozen=True, slots=True)
class UserConstraints:
    blocked_times: tuple[BlockedTime, ...] = ()
    instructor_rules: tuple[InstructorRule, ...] = ()
    locked_option_ids: frozenset[str] = frozenset()
    forbidden_option_ids: frozenset[str] = frozenset()
    explicitly_allowed_course_ids: frozenset[str] = frozenset()
    confirmed_unknown_time_option_ids: frozenset[str] = frozenset()
    prefer_compact_days: bool = False
    compact_day_penalty: int = 10
    recommended_cohort: str | None = None
    non_recommended_cohort_penalty: int = 200
    phase: SelectionPhase = SelectionPhase.PRESELECTION

    def __post_init__(self) -> None:
        if self.compact_day_penalty < 0:
            raise ValueError("compact-day penalty cannot be negative")
        if self.non_recommended_cohort_penalty < 0:
            raise ValueError("recommended-cohort penalty cannot be negative")


def _normalize_instructor(value: str) -> str:
    return "".join(value.split()).casefold()
