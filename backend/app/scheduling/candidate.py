"""Candidate filtering with structured, user-facing rejection reasons."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.constraints import ConstraintStrength, SelectionPhase
from ..domain.models import AvailabilityStatus, SectionOption
from ..domain.planning import RejectionReason, SchedulingProblem

ACKNOWLEDGED_UNKNOWN_TIME_PENALTY = 1_000
CONFIRMATION_REQUIRED_OPTION_PENALTY = 500


@dataclass(frozen=True, slots=True)
class OptionEvaluation:
    option: SectionOption
    hard_rejections: tuple[RejectionReason, ...] = ()
    soft_reasons: tuple[RejectionReason, ...] = ()
    soft_penalty: int = 0

    @property
    def accepted(self) -> bool:
        return not self.hard_rejections


@dataclass(frozen=True, slots=True)
class CandidateAudit:
    evaluations: tuple[OptionEvaluation, ...]

    @property
    def accepted_options(self) -> tuple[SectionOption, ...]:
        return tuple(evaluation.option for evaluation in self.evaluations if evaluation.accepted)

    @property
    def rejected_options(
        self,
    ) -> tuple[tuple[str, tuple[RejectionReason, ...]], ...]:
        return tuple(
            (evaluation.option.id, evaluation.hard_rejections)
            for evaluation in self.evaluations
            if not evaluation.accepted
        )

    def by_id(self) -> dict[str, OptionEvaluation]:
        return {evaluation.option.id: evaluation for evaluation in self.evaluations}


def evaluate_candidates(problem: SchedulingProblem) -> CandidateAudit:
    """Apply option-local hard filters and calculate option-local penalties.

    Pairwise timetable conflicts are intentionally left for the CP-SAT model;
    rejecting both sides here would discard valid alternatives.
    """

    evaluations: list[OptionEvaluation] = []
    constraints = problem.constraints
    course_by_id = {course.id: course for course in problem.courses}

    for option in problem.options:
        hard: list[RejectionReason] = []
        soft: list[RejectionReason] = []
        soft_penalty = 0
        course = course_by_id[option.course_id]

        if (
            course.availability is not AvailabilityStatus.AVAILABLE
            and course.id not in constraints.explicitly_allowed_course_ids
        ):
            status_label = (
                "需要确认"
                if course.availability is AvailabilityStatus.NEEDS_CONFIRMATION
                else "不可用"
            )
            hard.append(
                RejectionReason(
                    code=(
                        "COURSE_NEEDS_CONFIRMATION"
                        if course.availability is AvailabilityStatus.NEEDS_CONFIRMATION
                        else "COURSE_UNAVAILABLE"
                    ),
                    message=(
                        f"课程 {course.code} 当前标记为{status_label}"
                        + (f"：{course.availability_reason}" if course.availability_reason else "")
                        + "；需显式确认后才可参与排课"
                    ),
                    related_ids=(course.id, option.id),
                )
            )

        if option.has_unknown_time:
            unknown_reason = RejectionReason(
                code="UNKNOWN_MEETING_TIME",
                message=(
                    f"教学班方案 {option.id} 含集中实践、日期范围或待通知时段，"
                    "无法自动证明与其他课程无冲突"
                ),
                related_ids=(option.id,),
            )
            if option.id not in constraints.confirmed_unknown_time_option_ids:
                hard.append(unknown_reason)
            else:
                soft.append(
                    RejectionReason(
                        code="ACKNOWLEDGED_UNKNOWN_MEETING_TIME",
                        message=(
                            f"教学班方案 {option.id} 的非精确时间已由用户确认；"
                            "仍需在学校发布具体安排后复核"
                        ),
                        related_ids=(option.id,),
                    )
                )
                soft_penalty += ACKNOWLEDGED_UNKNOWN_TIME_PENALTY

        if option.requires_confirmation:
            soft.append(
                RejectionReason(
                    code="ACKNOWLEDGED_DATA_QUALITY_RISK",
                    message=(
                        f"教学班方案 {option.id} 来自旧版快照或低置信度记录；"
                        "虽已确认可参与排课，仍优先推荐可靠教学班"
                    ),
                    related_ids=(option.id,),
                )
            )
            soft_penalty += CONFIRMATION_REQUIRED_OPTION_PENALTY

        if option.id in constraints.forbidden_option_ids:
            hard.append(
                RejectionReason(
                    code="FORBIDDEN_OPTION",
                    message=f"教学班方案 {option.id} 已被用户排除",
                    related_ids=(option.id,),
                )
            )

        if constraints.phase is not SelectionPhase.PRESELECTION:
            full_sections = tuple(
                section
                for section in option.sections
                if section.remaining_capacity is not None and section.remaining_capacity <= 0
            )
            if full_sections:
                hard.append(
                    RejectionReason(
                        code="NO_REMAINING_CAPACITY",
                        message=(
                            f"教学班方案 {option.id} 在当前选课阶段不可超容量；"
                            f"已满班组：{', '.join(s.section_code for s in full_sections)}"
                        ),
                        related_ids=(option.id,) + tuple(section.id for section in full_sections),
                    )
                )

        if constraints.recommended_cohort:
            recommended = tuple(
                cohort
                for section in option.sections
                for cohort in section.recommended_cohorts
                if cohort.strip()
            )
            if recommended and not any(
                _cohort_matches(constraints.recommended_cohort, value)
                for value in recommended
            ):
                soft.append(
                    RejectionReason(
                        code="NON_RECOMMENDED_COHORT",
                        message=(
                            f"教学班推荐组成 {', '.join(recommended)} 未包含你的行政班 "
                            f"{constraints.recommended_cohort}；仍可作为跨班备选，但选课资格需在教务系统核对"
                        ),
                        related_ids=(option.id,),
                    )
                )
                soft_penalty += constraints.non_recommended_cohort_penalty

        for rule in constraints.instructor_rules:
            matching = tuple(
                instructor for instructor in option.instructors if rule.matches(instructor)
            )
            if not matching:
                continue
            reason = RejectionReason(
                code=(
                    "HARD_INSTRUCTOR"
                    if rule.strength is ConstraintStrength.HARD
                    else "SOFT_INSTRUCTOR"
                ),
                message=(
                    f"教学班教师 {', '.join(matching)} 命中规则“{rule.label or rule.instructor}”"
                ),
                related_ids=(option.id, rule.id),
            )
            if rule.strength is ConstraintStrength.HARD:
                hard.append(reason)
            else:
                soft.append(reason)
                soft_penalty += rule.penalty

        for blocked in constraints.blocked_times:
            blocked_meeting = blocked.as_meeting()
            overlapping = tuple(
                meeting for meeting in option.meetings if meeting.overlaps(blocked_meeting)
            )
            if not overlapping:
                continue
            overlap_weeks = sorted(
                {
                    week
                    for meeting in overlapping
                    for week in meeting.weeks.intersection(blocked.weeks).weeks
                }
            )
            reason = RejectionReason(
                code=(
                    "HARD_BLOCKED_TIME"
                    if blocked.strength is ConstraintStrength.HARD
                    else "SOFT_BLOCKED_TIME"
                ),
                message=(
                    f"教学班在星期{blocked.weekday}第"
                    f"{blocked.start_period}-{blocked.end_period}节与"
                    f"“{blocked.label or blocked.id}”冲突"
                    f"（第{_format_weeks(overlap_weeks)}周）"
                ),
                related_ids=(option.id, blocked.id),
            )
            if blocked.strength is ConstraintStrength.HARD:
                hard.append(reason)
            else:
                soft.append(reason)
                soft_penalty += blocked.penalty

        evaluations.append(
            OptionEvaluation(
                option=option,
                hard_rejections=tuple(hard),
                soft_reasons=tuple(soft),
                soft_penalty=soft_penalty,
            )
        )

    return CandidateAudit(tuple(evaluations))


def _format_weeks(weeks: list[int]) -> str:
    return "、".join(str(week) for week in weeks) if weeks else "未知"


def _cohort_matches(expected: str, registered: str) -> bool:
    expected_key = "".join(character for character in expected if character.isalnum()).casefold()
    registered_key = "".join(
        character for character in registered if character.isalnum()
    ).casefold()
    return bool(expected_key) and expected_key in registered_key
