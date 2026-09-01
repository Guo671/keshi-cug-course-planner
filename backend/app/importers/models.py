"""Lossless intermediate records produced by schedule importers.

These records intentionally sit outside the scheduling domain.  Imported data
is evidence, not truth: every derived value keeps its source, raw cell text,
confidence and parser issues so a later application layer can decide whether
it is safe to schedule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class TimePrecision(StrEnum):
    EXACT_SLOT = "exact_slot"
    WEEK_ONLY = "week_only"
    TBD = "tbd"


@dataclass(frozen=True, slots=True)
class CellReference:
    sheet: str
    row: int
    column: int

    def __post_init__(self) -> None:
        if self.row < 1 or self.column < 1:
            raise ValueError("cell coordinates are one-based and must be positive")

    @property
    def a1(self) -> str:
        number = self.column
        letters = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            letters = chr(65 + remainder) + letters
        return f"{letters}{self.row}"


@dataclass(frozen=True, slots=True)
class ImportIssue:
    code: str
    message: str
    severity: IssueSeverity = IssueSeverity.WARNING
    cell: CellReference | None = None


@dataclass(frozen=True, slots=True)
class SourceDocument:
    snapshot_id: str
    kind: str
    container: str
    original_entry_name: str | None
    safe_filename: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ImportedMeeting:
    raw: str
    source: SourceDocument
    cell: CellReference
    instructors_raw: str
    instructors: tuple[str, ...]
    week_expression_raw: str
    weeks: tuple[int, ...]
    weekday: int | None
    start_period: int | None
    end_period: int | None
    campus: str | None
    room: str | None
    location_raw: str | None
    class_label_raw: str
    section_code: str | None
    section_base_code: str | None
    section_suffix: str | None
    class_composition_raw: str
    class_composition: tuple[str, ...]
    enrolled_count_snapshot: int | None
    assessment: str | None
    precision: TimePrecision
    confidence: Confidence
    issues: tuple[ImportIssue, ...] = ()
    # The source workbook contains a current selected-student count, never a
    # capacity.  Keeping this field non-initialisable prevents accidental
    # positional assignment or inference during import.
    capacity: None = field(default=None, init=False)

    @property
    def identity_key(self) -> tuple[str, str]:
        token = self.section_code or _identity_text(self.class_label_raw)
        return token.casefold(), _composition_identity(
            self.class_composition, self.class_composition_raw
        )

    @property
    def reliable_for_scheduling(self) -> bool:
        if self.precision is not TimePrecision.EXACT_SLOT:
            return False
        if not self.weeks or self.weekday is None:
            return False
        if self.start_period is None or self.end_period is None:
            return False
        blocking_codes = {
            "class_identity_missing",
            "ordinary_fields_unrecoverable",
            "section_hyphen_missing",
            "week_expression_invalid",
        }
        return not any(
            issue.severity is IssueSeverity.ERROR or issue.code in blocking_codes
            for issue in self.issues
        )


@dataclass(frozen=True, slots=True)
class ImportedTeachingClass:
    course_code: str
    class_label_raw: str
    section_code: str | None
    section_base_code: str | None
    section_suffix: str | None
    class_composition_raw: str
    class_composition: tuple[str, ...]
    instructors: tuple[str, ...]
    meetings: tuple[ImportedMeeting, ...]
    enrolled_count_snapshot: int | None
    assessment: str | None
    source: SourceDocument
    confidence: Confidence
    issues: tuple[ImportIssue, ...] = ()
    needs_confirmation: bool = False
    reliable_for_scheduling: bool = True
    capacity: None = field(default=None, init=False)

    @property
    def identity_key(self) -> tuple[str, str, str]:
        token = self.section_code or _identity_text(self.class_label_raw)
        return (
            self.course_code.casefold(),
            token.casefold(),
            _composition_identity(self.class_composition, self.class_composition_raw),
        )


@dataclass(frozen=True, slots=True)
class ImportedCourseSchedule:
    course_code: str
    course_name: str
    course_name_from_filename_raw: str | None
    course_name_from_title_raw: str | None
    offering_college: str | None
    term: str | None
    term_start: str | None
    term_end: str | None
    total_weeks: int | None
    print_date: str | None
    export_token: str | None
    source: SourceDocument
    teaching_classes: tuple[ImportedTeachingClass, ...]
    issues: tuple[ImportIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    snapshot_id: str
    courses: tuple[ImportedCourseSchedule, ...]
    issues: tuple[ImportIssue, ...] = ()

    @property
    def teaching_classes(self) -> tuple[ImportedTeachingClass, ...]:
        return tuple(section for course in self.courses for section in course.teaching_classes)


def _identity_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _composition_identity(values: tuple[str, ...], raw: str) -> str:
    if not values:
        return _identity_text(raw)
    return ";".join(sorted(_identity_text(value) for value in values))


def lower_confidence(*values: Confidence) -> Confidence:
    rank = {Confidence.HIGH: 2, Confidence.MEDIUM: 1, Confidence.LOW: 0}
    return min(values, key=rank.__getitem__) if values else Confidence.HIGH
