import app.scheduling.solver as solver_module
import pytest
from app.domain import (
    AvailabilityStatus,
    BlockedTime,
    ConstraintStrength,
    Course,
    CourseRequest,
    InstructorRule,
    Meeting,
    SchedulingProblem,
    SectionOption,
    SelectionPhase,
    TeachingSection,
    TimePrecision,
    UserConstraints,
    WeekMask,
)
from app.scheduling import (
    OrToolsUnavailableError,
    ScheduleSolver,
    SolverConfig,
    evaluate_candidates,
)


def _option(
    option_id: str,
    *,
    instructor: str,
    weeks: WeekMask | None = None,
) -> SectionOption:
    section = TeachingSection(
        id=f"section-{option_id}",
        course_id="course",
        section_code=option_id,
        instructors=(instructor,),
        meetings=(
            Meeting(
                weeks=weeks or WeekMask.from_range(1, 16),
                weekday=1,
                start_period=1,
                end_period=2,
            ),
        ),
    )
    return SectionOption(option_id, "course", (section,))


def _problem(options: tuple[SectionOption, ...], constraints: UserConstraints) -> SchedulingProblem:
    return SchedulingProblem(
        courses=(Course("course", "C001", "测试课程", 2),),
        requests=(CourseRequest("course", priority=10),),
        options=options,
        constraints=constraints,
    )


def test_hard_and_soft_instructor_rules_are_distinguished() -> None:
    blocked_teacher = _option("hard", instructor="  张老师 ")
    disliked_teacher = _option("soft", instructor="李老师")
    audit = evaluate_candidates(
        _problem(
            (blocked_teacher, disliked_teacher),
            UserConstraints(
                instructor_rules=(
                    InstructorRule(
                        "never-zhang",
                        "张老师",
                        strength=ConstraintStrength.HARD,
                    ),
                    InstructorRule(
                        "avoid-li",
                        "李老师",
                        strength=ConstraintStrength.SOFT,
                        penalty=37,
                    ),
                )
            ),
        )
    )

    by_id = audit.by_id()
    assert not by_id["hard"].accepted
    assert by_id["hard"].hard_rejections[0].code == "HARD_INSTRUCTOR"
    assert by_id["soft"].accepted
    assert by_id["soft"].soft_penalty == 37
    assert by_id["soft"].soft_reasons[0].code == "SOFT_INSTRUCTOR"


def test_blocked_time_checks_actual_week_intersection() -> None:
    only_week_three = _option(
        "week-three",
        instructor="王老师",
        weeks=WeekMask.from_weeks([3]),
    )
    no_overlap = evaluate_candidates(
        _problem(
            (only_week_three,),
            UserConstraints(
                blocked_times=(
                    BlockedTime(
                        "week-two-exam",
                        weekday=1,
                        start_period=1,
                        end_period=2,
                        weeks=WeekMask.from_weeks([2]),
                    ),
                )
            ),
        )
    )
    overlap = evaluate_candidates(
        _problem(
            (only_week_three,),
            UserConstraints(
                blocked_times=(
                    BlockedTime(
                        "week-three-exam",
                        weekday=1,
                        start_period=2,
                        end_period=3,
                        weeks=WeekMask.from_weeks([3]),
                    ),
                )
            ),
        )
    )

    assert no_overlap.by_id()["week-three"].accepted
    rejected = overlap.by_id()["week-three"]
    assert not rejected.accepted
    assert rejected.hard_rejections[0].code == "HARD_BLOCKED_TIME"
    assert "第3周" in rejected.hard_rejections[0].message


def test_soft_blocked_time_adds_penalty_without_rejecting_option() -> None:
    option = _option("soft-overlap", instructor="王老师")
    audit = evaluate_candidates(
        _problem(
            (option,),
            UserConstraints(
                blocked_times=(
                    BlockedTime(
                        "prefer-free",
                        weekday=1,
                        start_period=1,
                        end_period=2,
                        weeks=WeekMask.from_range(1, 16),
                        strength=ConstraintStrength.SOFT,
                        penalty=23,
                    ),
                )
            ),
        )
    )

    evaluation = audit.by_id()["soft-overlap"]
    assert evaluation.accepted
    assert evaluation.soft_penalty == 23
    assert evaluation.soft_reasons[0].code == "SOFT_BLOCKED_TIME"


def test_unknown_meeting_time_requires_explicit_confirmation_but_async_does_not() -> None:
    week_only_section = TeachingSection(
        id="section-practice",
        course_id="course",
        section_code="practice",
        instructors=("实践教师",),
        meetings=(
            Meeting(
                weeks=WeekMask.from_range(19, 21),
                precision=TimePrecision.WEEK_ONLY,
            ),
        ),
    )
    async_section = TeachingSection(
        id="section-async",
        course_id="course",
        section_code="async",
        instructors=("线上教师",),
        meetings=(
            Meeting(
                weeks=WeekMask.from_range(1, 16),
                precision=TimePrecision.ASYNC,
            ),
        ),
    )
    practice = SectionOption("practice", "course", (week_only_section,))
    asynchronous = SectionOption("async", "course", (async_section,))

    default_audit = evaluate_candidates(_problem((practice, asynchronous), UserConstraints()))
    confirmed_audit = evaluate_candidates(
        _problem(
            (practice, asynchronous),
            UserConstraints(confirmed_unknown_time_option_ids=frozenset({"practice"})),
        )
    )

    assert not default_audit.by_id()["practice"].accepted
    assert default_audit.by_id()["practice"].hard_rejections[0].code == "UNKNOWN_MEETING_TIME"
    assert default_audit.by_id()["async"].accepted
    assert confirmed_audit.by_id()["practice"].accepted
    assert (
        confirmed_audit.by_id()["practice"].soft_reasons[0].code
        == "ACKNOWLEDGED_UNKNOWN_MEETING_TIME"
    )


def test_capacity_is_phase_aware() -> None:
    section = TeachingSection(
        id="section-full",
        course_id="course",
        section_code="0001",
        instructors=("教师",),
        meetings=(
            Meeting(
                weeks=WeekMask.from_range(1, 16),
                weekday=2,
                start_period=1,
                end_period=2,
            ),
        ),
        capacity=30,
        enrolled_count=30,
    )
    option = SectionOption("full", "course", (section,))

    preselection = evaluate_candidates(
        _problem(
            (option,),
            UserConstraints(phase=SelectionPhase.PRESELECTION),
        )
    )
    confirmation = evaluate_candidates(
        _problem(
            (option,),
            UserConstraints(phase=SelectionPhase.CONFIRMATION),
        )
    )

    assert preselection.by_id()["full"].accepted
    assert not confirmation.by_id()["full"].accepted
    assert confirmation.by_id()["full"].hard_rejections[0].code == "NO_REMAINING_CAPACITY"


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (AvailabilityStatus.NEEDS_CONFIRMATION, "COURSE_NEEDS_CONFIRMATION"),
        (AvailabilityStatus.UNAVAILABLE, "COURSE_UNAVAILABLE"),
    ],
)
def test_strategy_a_excludes_legacy_or_unavailable_course_until_confirmed(
    status: AvailabilityStatus, expected_code: str
) -> None:
    option = _option("legacy", instructor="王老师")
    course = Course(
        "course",
        "OLD001",
        "旧版独有课程",
        2,
        availability=status,
        availability_reason="仅见于旧版培养方案",
    )
    base = dict(
        courses=(course,),
        requests=(CourseRequest("course", priority=10),),
        options=(option,),
    )

    blocked = evaluate_candidates(SchedulingProblem(**base))
    allowed = evaluate_candidates(
        SchedulingProblem(
            **base,
            constraints=UserConstraints(explicitly_allowed_course_ids=frozenset({"course"})),
        )
    )

    assert not blocked.by_id()["legacy"].accepted
    assert blocked.by_id()["legacy"].hard_rejections[0].code == expected_code
    assert "需显式确认" in blocked.by_id()["legacy"].hard_rejections[0].message
    assert allowed.by_id()["legacy"].accepted


def test_missing_ortools_uses_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(solver_module, "cp_model", None)
    problem = _problem((_option("available", instructor="王老师"),), UserConstraints())

    first = ScheduleSolver(SolverConfig(max_solutions=1)).solve(problem)
    second = ScheduleSolver(SolverConfig(max_solutions=1)).solve(problem)

    assert first.plans[0].selected_option_ids == ("available",)
    assert first.plans == second.plans


def test_missing_ortools_error_is_actionable_when_fallback_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(solver_module, "cp_model", None)
    problem = _problem((_option("available", instructor="王老师"),), UserConstraints())

    with pytest.raises(OrToolsUnavailableError, match="OR-Tools"):
        ScheduleSolver(SolverConfig(allow_deterministic_fallback=False)).solve(problem)
