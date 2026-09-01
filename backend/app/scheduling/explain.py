"""Human-readable explanations and first-pass infeasibility diagnostics."""

from __future__ import annotations

from itertools import combinations, product

from ..domain.conflicts import option_conflicts, options_overlap
from ..domain.models import SectionOption
from ..domain.planning import (
    CourseExplanation,
    Diagnostic,
    SchedulingProblem,
)
from .candidate import CandidateAudit


def diagnose_infeasibility(
    problem: SchedulingProblem,
    audit: CandidateAudit,
    *,
    include_generic: bool = False,
) -> tuple[Diagnostic, ...]:
    """Find actionable contradictions detectable without relaxing constraints.

    This deliberately returns concrete local causes first.  CP-SAT remains the
    source of truth for higher-order combinations; if it proves infeasibility
    and no local cause exists, a generic diagnostic is appended.
    """

    diagnostics: list[Diagnostic] = []
    all_options = {option.id: option for option in problem.options}
    evaluation_by_id = audit.by_id()
    accepted_by_course: dict[str, list[SectionOption]] = {}
    for option in audit.accepted_options:
        accepted_by_course.setdefault(option.course_id, []).append(option)

    for locked_id in sorted(problem.constraints.locked_option_ids):
        if locked_id not in all_options:
            diagnostics.append(
                Diagnostic(
                    code="UNKNOWN_LOCKED_OPTION",
                    message=f"锁定的教学班方案 {locked_id} 不存在于当前数据快照",
                    related_ids=(locked_id,),
                )
            )
            continue
        evaluation = evaluation_by_id[locked_id]
        if not evaluation.accepted:
            reasons = "；".join(r.message for r in evaluation.hard_rejections)
            diagnostics.append(
                Diagnostic(
                    code="LOCKED_OPTION_REJECTED",
                    message=f"锁定的教学班方案 {locked_id} 违反硬约束：{reasons}",
                    related_ids=(locked_id,),
                )
            )

    locked_options = [
        all_options[option_id]
        for option_id in problem.constraints.locked_option_ids
        if option_id in all_options and evaluation_by_id[option_id].accepted
    ]
    for left, right in combinations(locked_options, 2):
        if left.course_id == right.course_id:
            diagnostics.append(
                Diagnostic(
                    code="MULTIPLE_LOCKS_FOR_COURSE",
                    message=(
                        f"同一课程同时锁定了 {left.id} 和 {right.id}，"
                        "但每门课程最多选择一个教学班方案"
                    ),
                    related_ids=(left.id, right.id, left.course_id),
                )
            )
        elif options_overlap(left, right):
            diagnostics.append(
                Diagnostic(
                    code="LOCKED_OPTIONS_CONFLICT",
                    message=_pair_conflict_message("两个锁定教学班方案发生时间冲突", left, right),
                    related_ids=(left.id, right.id),
                )
            )

    required_requests = [request for request in problem.requests if request.required]
    for request in required_requests:
        candidates = accepted_by_course.get(request.course_id, [])
        if candidates:
            continue
        rejected = [
            evaluation
            for evaluation in audit.evaluations
            if evaluation.option.course_id == request.course_id
        ]
        if not rejected:
            detail = "当前课程库没有该课程的教学班"
            related: tuple[str, ...] = (request.course_id,)
        else:
            reason_messages = [
                reason.message for evaluation in rejected for reason in evaluation.hard_rejections
            ]
            detail = "；".join(reason_messages)
            related = (request.course_id,) + tuple(evaluation.option.id for evaluation in rejected)
        diagnostics.append(
            Diagnostic(
                code="REQUIRED_COURSE_HAS_NO_CANDIDATE",
                message=f"必须安排的课程 {request.course_id} 无可用教学班：{detail}",
                related_ids=related,
            )
        )

    # Detect a useful and common two-course unsatisfiable core.  More complex
    # cores are left to CP-SAT and reported generically in this first version.
    required_with_candidates = [
        request for request in required_requests if accepted_by_course.get(request.course_id)
    ]
    for left_request, right_request in combinations(required_with_candidates, 2):
        left_options = accepted_by_course[left_request.course_id]
        right_options = accepted_by_course[right_request.course_id]
        if all(
            options_overlap(left, right) for left, right in product(left_options, right_options)
        ):
            diagnostics.append(
                Diagnostic(
                    code="REQUIRED_COURSES_ALWAYS_CONFLICT",
                    message=(
                        f"必须课程 {left_request.course_id} 与 "
                        f"{right_request.course_id} 的所有候选教学班均冲突"
                    ),
                    related_ids=(
                        left_request.course_id,
                        right_request.course_id,
                    ),
                )
            )

    if not diagnostics and include_generic:
        diagnostics.append(
            Diagnostic(
                code="SOLVER_INFEASIBLE",
                message=(
                    "硬约束组合无解；请依次检查锁定教学班、必须课程、教师绝对排除和绝对禁排时间"
                ),
            )
        )
    return tuple(diagnostics)


def build_plan_explanations(
    problem: SchedulingProblem,
    audit: CandidateAudit,
    selected_option_ids: tuple[str, ...],
) -> tuple[CourseExplanation, ...]:
    option_by_id = {option.id: option for option in problem.options}
    selected_options = [option_by_id[option_id] for option_id in selected_option_ids]
    explanations: list[CourseExplanation] = []

    for request in problem.requests:
        selected = next(
            (option for option in selected_options if option.course_id == request.course_id),
            None,
        )
        if selected is not None:
            selected_messages = [f"已安排教学班方案 {selected.id}"]
            if selected.id in problem.constraints.locked_option_ids:
                selected_messages.append("该教学班由用户锁定")
            evaluation = audit.by_id()[selected.id]
            selected_messages.extend(reason.message for reason in evaluation.soft_reasons)
            explanations.append(
                CourseExplanation(
                    course_id=request.course_id,
                    selected_option_id=selected.id,
                    messages=tuple(selected_messages),
                )
            )
            continue

        unscheduled_messages: list[str] = []
        evaluations = [
            evaluation
            for evaluation in audit.evaluations
            if evaluation.option.course_id == request.course_id
        ]
        for evaluation in evaluations:
            unscheduled_messages.extend(reason.message for reason in evaluation.hard_rejections)
            if evaluation.accepted:
                conflicting_selected = [
                    chosen
                    for chosen in selected_options
                    if options_overlap(evaluation.option, chosen)
                ]
                for chosen in conflicting_selected:
                    unscheduled_messages.append(
                        _pair_conflict_message(
                            f"候选 {evaluation.option.id} 被已选课程占用",
                            evaluation.option,
                            chosen,
                        )
                    )
        if not unscheduled_messages:
            unscheduled_messages.append("该课程优先级低于当前已排课程，保留到替代方案")
        explanations.append(
            CourseExplanation(
                course_id=request.course_id,
                selected_option_id=None,
                messages=tuple(dict.fromkeys(unscheduled_messages)),
            )
        )

    return tuple(explanations)


def _pair_conflict_message(prefix: str, left: SectionOption, right: SectionOption) -> str:
    conflicts = option_conflicts(left, right)
    if not conflicts:
        return f"{prefix}：{left.id} / {right.id}"
    weeks = sorted({week for conflict in conflicts for week in conflict.overlapping_weeks})
    first = conflicts[0]
    return (
        f"{prefix}：{left.id} 与 {right.id} 在星期{first.left.weekday}"
        f"第{first.left.start_period}-{first.left.end_period}节冲突"
        f"（第{'、'.join(str(week) for week in weeks)}周）"
    )
