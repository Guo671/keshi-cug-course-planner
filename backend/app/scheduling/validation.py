"""Independent post-solve validation of every hard invariant."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ..domain.conflicts import options_overlap
from ..domain.planning import SchedulePlan, SchedulingProblem
from .candidate import evaluate_candidates


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    related_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues


def validate_plan(problem: SchedulingProblem, plan: SchedulePlan) -> ValidationReport:
    issues: list[ValidationIssue] = []
    option_by_id = {option.id: option for option in problem.options}

    if len(plan.selected_option_ids) != len(set(plan.selected_option_ids)):
        issues.append(
            ValidationIssue(
                code="DUPLICATE_SELECTED_OPTION",
                message="求解结果重复包含同一教学班方案",
            )
        )

    unknown = [option_id for option_id in plan.selected_option_ids if option_id not in option_by_id]
    if unknown:
        issues.append(
            ValidationIssue(
                code="UNKNOWN_SELECTED_OPTION",
                message="求解结果包含当前数据中不存在的教学班方案",
                related_ids=tuple(unknown),
            )
        )

    selected = [
        option_by_id[option_id]
        for option_id in plan.selected_option_ids
        if option_id in option_by_id
    ]
    selected_ids = {option.id for option in selected}
    selected_by_course: dict[str, list[str]] = {}
    for option in selected:
        selected_by_course.setdefault(option.course_id, []).append(option.id)

    for course_id, option_ids in selected_by_course.items():
        if len(option_ids) > 1:
            issues.append(
                ValidationIssue(
                    code="MULTIPLE_OPTIONS_FOR_COURSE",
                    message=f"课程 {course_id} 同时选择了多个教学班方案",
                    related_ids=tuple(option_ids),
                )
            )

    for request in problem.requests:
        if request.required and request.course_id not in selected_by_course:
            issues.append(
                ValidationIssue(
                    code="MISSING_REQUIRED_COURSE",
                    message=f"必须课程 {request.course_id} 未排入",
                    related_ids=(request.course_id,),
                )
            )

    missing_locks = problem.constraints.locked_option_ids - selected_ids
    if missing_locks:
        issues.append(
            ValidationIssue(
                code="MISSING_LOCKED_OPTION",
                message="求解结果没有包含全部锁定教学班",
                related_ids=tuple(sorted(missing_locks)),
            )
        )

    audit = evaluate_candidates(problem)
    evaluation_by_id = audit.by_id()
    for option in selected:
        evaluation = evaluation_by_id[option.id]
        if not evaluation.accepted:
            issues.append(
                ValidationIssue(
                    code="SELECTED_OPTION_VIOLATES_HARD_CONSTRAINT",
                    message=(
                        f"教学班方案 {option.id} 违反硬约束："
                        + "；".join(reason.message for reason in evaluation.hard_rejections)
                    ),
                    related_ids=(option.id,),
                )
            )

    for left, right in combinations(selected, 2):
        if options_overlap(left, right):
            issues.append(
                ValidationIssue(
                    code="SELECTED_OPTIONS_CONFLICT",
                    message=f"已选教学班方案 {left.id} 与 {right.id} 时间冲突",
                    related_ids=(left.id, right.id),
                )
            )

    expected_unscheduled = {
        request.course_id
        for request in problem.requests
        if request.course_id not in selected_by_course
    }
    if set(plan.unscheduled_course_ids) != expected_unscheduled:
        issues.append(
            ValidationIssue(
                code="UNSCHEDULED_COURSES_MISMATCH",
                message="未排入课程列表与教学班选择不一致",
                related_ids=tuple(sorted(expected_unscheduled)),
            )
        )

    return ValidationReport(tuple(issues))
