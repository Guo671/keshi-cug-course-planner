"""Plan-level soft scoring shared by CP-SAT and deterministic fallback."""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.models import SectionOption
from ..domain.planning import SchedulingProblem


def exact_teaching_days(options: Iterable[SectionOption]) -> frozenset[int]:
    return frozenset(
        meeting.weekday
        for option in options
        for meeting in option.meetings
        if meeting.is_exact and meeting.weekday is not None
    )


def plan_level_soft_penalty(
    problem: SchedulingProblem,
    selected_options: Iterable[SectionOption],
) -> int:
    if not problem.constraints.prefer_compact_days:
        return 0
    return problem.constraints.compact_day_penalty * len(exact_teaching_days(selected_options))
