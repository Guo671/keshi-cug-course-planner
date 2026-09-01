"""Public, framework-independent scheduling domain API."""

from .conflicts import MeetingConflict, meeting_conflict, option_conflicts, options_overlap
from .constraints import (
    BlockedTime,
    ConstraintStrength,
    InstructorRule,
    SelectionPhase,
    UserConstraints,
)
from .models import (
    AvailabilityStatus,
    Course,
    Meeting,
    SectionOption,
    TeachingSection,
    TimePrecision,
)
from .planning import (
    CourseExplanation,
    CourseRequest,
    Diagnostic,
    RejectionReason,
    SchedulePlan,
    SchedulingProblem,
    SolveResult,
    SolveStatus,
)
from .week_mask import MAX_ACADEMIC_WEEK, WeekMask, WeekParity

__all__ = [
    "BlockedTime",
    "AvailabilityStatus",
    "ConstraintStrength",
    "Course",
    "CourseExplanation",
    "CourseRequest",
    "Diagnostic",
    "InstructorRule",
    "MAX_ACADEMIC_WEEK",
    "Meeting",
    "MeetingConflict",
    "RejectionReason",
    "SchedulePlan",
    "SchedulingProblem",
    "SectionOption",
    "SelectionPhase",
    "SolveResult",
    "SolveStatus",
    "TeachingSection",
    "TimePrecision",
    "UserConstraints",
    "WeekMask",
    "WeekParity",
    "meeting_conflict",
    "option_conflicts",
    "options_overlap",
]
