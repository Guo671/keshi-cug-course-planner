"""Work estimates checked before expanding user-controlled scheduling data."""

from ..domain.planning import SchedulingProblem

MAX_OPTIONS = 2000
MAX_MEETINGS = 10000
MAX_OCCURRENCES = 50000
MAX_SLOT_REFERENCES = 250000


def size_limit_message(problem: SchedulingProblem) -> str | None:
    meetings = sum(len(o.meetings) for o in problem.options)
    occurrences = sum(len(m.weeks.weeks) for o in problem.options for m in o.meetings if m.is_exact)
    references = sum(
        len(m.weeks.weeks) * ((m.end_period or 1) - (m.start_period or 1) + 1)
        for o in problem.options
        for m in o.meetings
        if m.is_exact
    )
    if (
        len(problem.options) > MAX_OPTIONS
        or meetings > MAX_MEETINGS
        or occurrences > MAX_OCCURRENCES
        or references > MAX_SLOT_REFERENCES
    ):
        return (
            "候选教学班或时段规模超过本机保护上限，请减少不需要的备选班后再试。"
            f"当前 {len(problem.options)} 个候选、{meetings} 条时段、"
            f"{occurrences} 次上课记录；上限分别为2000、10000、50000。"
        )
    return None
