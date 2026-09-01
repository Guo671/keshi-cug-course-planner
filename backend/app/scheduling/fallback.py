"""Deterministic branch-and-bound fallback when OR-Tools is unavailable."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.conflicts import options_overlap
from ..domain.models import SectionOption
from ..domain.planning import SchedulingProblem
from .candidate import CandidateAudit
from .scoring import plan_level_soft_penalty


@dataclass(frozen=True, slots=True)
class FallbackSearchResult:
    selected_option_ids: tuple[str, ...] | None
    coverage_score: int
    soft_penalty: int
    search_complete: bool
    nodes_visited: int


def deterministic_search(
    problem: SchedulingProblem,
    audit: CandidateAudit,
    *,
    prior_selections: list[tuple[str, ...]],
    min_option_difference: int,
    node_limit: int,
) -> FallbackSearchResult:
    """Find the lexicographically best plan with a deterministic DFS.

    The fallback has the same objective order as CP-SAT: maximize requested
    course priority, then minimize soft penalties.  A deterministic node cap
    prevents pathological catalogs from freezing a local application; a found
    incumbent is returned with ``search_complete=False`` rather than being
    misreported as optimal.
    """

    if node_limit < 1:
        raise ValueError("fallback node_limit must be at least 1")

    evaluation_by_id = audit.by_id()
    accepted_by_course: dict[str, list[SectionOption]] = {}
    for option in audit.accepted_options:
        accepted_by_course.setdefault(option.course_id, []).append(option)

    option_by_id = {option.id: option for option in audit.accepted_options}
    locked_by_course = {
        option_by_id[option_id].course_id: option_by_id[option_id]
        for option_id in problem.constraints.locked_option_ids
    }
    request_by_course = {request.course_id: request for request in problem.requests}
    ordered_requests = sorted(
        problem.requests,
        key=lambda request: (
            0 if request.course_id in locked_by_course else 1,
            0 if request.required else 1,
            -request.priority,
            request.course_id,
        ),
    )

    choices_by_course: dict[str, tuple[SectionOption | None, ...]] = {}
    for request in ordered_requests:
        if request.course_id in locked_by_course:
            choices: list[SectionOption | None] = [locked_by_course[request.course_id]]
        else:
            available_choices = sorted(
                accepted_by_course.get(request.course_id, []),
                key=lambda option: (
                    evaluation_by_id[option.id].soft_penalty,
                    option.id,
                ),
            )
            choices = list(available_choices)
            if not request.required:
                # Positive-priority choices are explored before omission so an
                # incumbent is useful even if the deterministic cap is reached.
                choices.append(None)
        choices_by_course[request.course_id] = tuple(choices)

    remaining_priority = [0] * (len(ordered_requests) + 1)
    for index in range(len(ordered_requests) - 1, -1, -1):
        request = ordered_requests[index]
        has_selectable_option = any(
            option is not None for option in choices_by_course[request.course_id]
        )
        remaining_priority[index] = remaining_priority[index + 1] + (
            request.priority if has_selectable_option else 0
        )

    conflict_ids: dict[str, frozenset[str]] = {}
    accepted = audit.accepted_options
    for left in accepted:
        conflict_ids[left.id] = frozenset(
            right.id
            for right in accepted
            if right.id != left.id
            and left.course_id != right.course_id
            and options_overlap(left, right)
        )

    nodes_visited = 0
    search_complete = True
    best_selection: frozenset[str] | None = None
    best_coverage = -1
    best_penalty = 0

    def violates_no_good(selected: frozenset[str]) -> bool:
        for previous in prior_selections:
            if not option_by_id:
                continue
            required_difference = min_option_difference
            if len(selected.symmetric_difference(previous)) < required_difference:
                return True
        return False

    def visit(
        index: int,
        selected: frozenset[str],
        coverage: int,
        penalty: int,
    ) -> None:
        nonlocal nodes_visited
        nonlocal search_complete
        nonlocal best_selection
        nonlocal best_coverage
        nonlocal best_penalty

        if nodes_visited >= node_limit:
            search_complete = False
            return
        nodes_visited += 1

        if coverage + remaining_priority[index] < best_coverage:
            return
        if index == len(ordered_requests):
            if violates_no_good(selected):
                return
            selected_options = [option_by_id[option_id] for option_id in selected]
            total_penalty = penalty + plan_level_soft_penalty(problem, selected_options)
            score = (coverage, -total_penalty)
            best_score = (best_coverage, -best_penalty)
            if best_selection is None or score > best_score:
                best_selection = selected
                best_coverage = coverage
                best_penalty = total_penalty
            return

        request = ordered_requests[index]
        for option in choices_by_course[request.course_id]:
            if option is None:
                visit(index + 1, selected, coverage, penalty)
                continue
            if any(chosen_id in conflict_ids[option.id] for chosen_id in selected):
                continue
            visit(
                index + 1,
                selected.union((option.id,)),
                coverage + request_by_course[option.course_id].priority,
                penalty + evaluation_by_id[option.id].soft_penalty,
            )

    visit(0, frozenset(), 0, 0)

    ordered_selection: tuple[str, ...] | None
    if best_selection is None:
        ordered_selection = None
    else:
        ordered_selection = tuple(
            option.id for option in problem.options if option.id in best_selection
        )
    return FallbackSearchResult(
        selected_option_ids=ordered_selection,
        coverage_score=max(best_coverage, 0),
        soft_penalty=best_penalty,
        search_complete=search_complete,
        nodes_visited=nodes_visited,
    )
