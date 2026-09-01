"""Pure scheduling domain entities.

The objects in this module deliberately contain no database or API concerns.
``SectionOption`` is the solver's atomic choice: it can represent a simple
teaching class or a verified bundle such as theory ``-0001`` plus laboratory
``-0001A``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import chain

from .week_mask import WeekMask


class TimePrecision(StrEnum):
    EXACT_SLOT = "exact_slot"
    DATE_RANGE = "date_range"
    WEEK_ONLY = "week_only"
    TBD = "tbd"
    ASYNC = "async"


class AvailabilityStatus(StrEnum):
    """Whether catalog evidence is strong enough to schedule a course.

    ``NEEDS_CONFIRMATION`` is the default status for courses seen only in an
    older curriculum version.  Strategy A keeps both non-available statuses
    out of candidate generation until the user explicitly confirms them.
    """

    AVAILABLE = "available"
    NEEDS_CONFIRMATION = "needs_confirmation"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Course:
    id: str
    code: str
    name: str
    credits: float = 0.0
    availability: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    availability_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("course id cannot be blank")
        if not self.code.strip():
            raise ValueError("course code cannot be blank")
        if not self.name.strip():
            raise ValueError("course name cannot be blank")
        if self.credits < 0:
            raise ValueError("course credits cannot be negative")


@dataclass(frozen=True, slots=True)
class Meeting:
    """One temporal segment of a teaching class.

    Exact meetings have a weekday and an inclusive period range.  Week-only,
    TBD and asynchronous records intentionally retain missing slot data rather
    than pretending that they are conflict-free exact meetings.
    """

    weeks: WeekMask
    weekday: int | None = None
    start_period: int | None = None
    end_period: int | None = None
    campus: str | None = None
    room: str | None = None
    precision: TimePrecision = TimePrecision.EXACT_SLOT
    source_ref: str | None = None

    def __post_init__(self) -> None:
        if self.precision is TimePrecision.EXACT_SLOT:
            if self.weekday is None:
                raise ValueError("an exact meeting requires a weekday")
            if self.start_period is None or self.end_period is None:
                raise ValueError("an exact meeting requires a period range")
        if self.weekday is not None and not 1 <= self.weekday <= 7:
            raise ValueError("weekday must be between 1 (Monday) and 7 (Sunday)")
        if (self.start_period is None) != (self.end_period is None):
            raise ValueError("start_period and end_period must be set together")
        if self.start_period is not None:
            if self.start_period < 1:
                raise ValueError("start_period must be positive")
            if self.end_period is None or self.end_period < self.start_period:
                raise ValueError("end_period cannot be before start_period")

    @property
    def is_exact(self) -> bool:
        return self.precision is TimePrecision.EXACT_SLOT

    def overlaps(self, other: Meeting) -> bool:
        """Return whether two exact meetings occupy the same teaching slot."""

        if not self.is_exact or not other.is_exact:
            return False
        if self.weekday != other.weekday:
            return False
        if not self.weeks.intersects(other.weeks):
            return False
        assert self.start_period is not None and self.end_period is not None
        assert other.start_period is not None and other.end_period is not None
        return self.start_period <= other.end_period and other.start_period <= self.end_period


@dataclass(frozen=True, slots=True)
class TeachingSection:
    id: str
    course_id: str
    section_code: str
    instructors: tuple[str, ...]
    meetings: tuple[Meeting, ...]
    capacity: int | None = None
    enrolled_count: int | None = None
    recommended_cohorts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("teaching section id cannot be blank")
        if not self.course_id.strip():
            raise ValueError("teaching section course_id cannot be blank")
        if not self.section_code.strip():
            raise ValueError("section_code cannot be blank")
        if self.capacity is not None and self.capacity < 0:
            raise ValueError("capacity cannot be negative")
        if self.enrolled_count is not None and self.enrolled_count < 0:
            raise ValueError("enrolled_count cannot be negative")

    @property
    def remaining_capacity(self) -> int | None:
        if self.capacity is None or self.enrolled_count is None:
            return None
        return self.capacity - self.enrolled_count


@dataclass(frozen=True, slots=True)
class SectionOption:
    """An atomic selectable teaching-class option, possibly a section bundle."""

    id: str
    course_id: str
    sections: tuple[TeachingSection, ...]
    label: str | None = None
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("section option id cannot be blank")
        if not self.course_id.strip():
            raise ValueError("section option course_id cannot be blank")
        if not self.sections:
            raise ValueError("section option must contain at least one section")
        mismatched = [
            section.id for section in self.sections if section.course_id != self.course_id
        ]
        if mismatched:
            raise ValueError(
                "all bundled sections must belong to the option course: " + ", ".join(mismatched)
            )

    @property
    def section_ids(self) -> tuple[str, ...]:
        return tuple(section.id for section in self.sections)

    @property
    def instructors(self) -> tuple[str, ...]:
        # dict preserves the source order while de-duplicating names.
        return tuple(
            dict.fromkeys(
                instructor.strip()
                for section in self.sections
                for instructor in section.instructors
                if instructor.strip()
            )
        )

    @property
    def meetings(self) -> tuple[Meeting, ...]:
        return tuple(chain.from_iterable(s.meetings for s in self.sections))

    @property
    def has_unknown_time(self) -> bool:
        return any(
            meeting.precision
            in {
                TimePrecision.DATE_RANGE,
                TimePrecision.WEEK_ONLY,
                TimePrecision.TBD,
            }
            for meeting in self.meetings
        )
