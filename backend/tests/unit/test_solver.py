from __future__ import annotations

import pytest

pytest.importorskip("ortools.sat.python.cp_model")

import app.scheduling.solver as solver_module
from app.domain import (
    ConstraintStrength,
    Course,
    CourseRequest,
    InstructorRule,
    Meeting,
    SchedulePlan,
    SchedulingProblem,
    SectionOption,
    SolveResult,
    SolveStatus,
    TeachingSection,
    TimePrecision,
    UserConstraints,
    WeekMask,
)
from app.scheduling import (
    ScheduleSolver,
    SolverConfig,
    validate_plan,
)


def _option(
    option_id: str,
    course_id: str,
    *,
    weekday: int,
    periods: tuple[int, int] = (1, 2),
    weeks: WeekMask | None = None,
    instructor: str = "普通教师",
    recommended_cohorts: tuple[str, ...] = (),
    precision: TimePrecision = TimePrecision.EXACT_SLOT,
    requires_confirmation: bool = False,
) -> SectionOption:
    meeting = (
        Meeting(
            weeks=weeks or WeekMask.from_range(1, 16),
            weekday=weekday,
            start_period=periods[0],
            end_period=periods[1],
        )
        if precision is TimePrecision.EXACT_SLOT
        else Meeting(
            weeks=weeks or WeekMask.from_range(1, 16),
            precision=precision,
        )
    )
    section = TeachingSection(
        id=f"section-{option_id}",
        course_id=course_id,
        section_code=option_id,
        instructors=(instructor,),
        meetings=(meeting,),
        recommended_cohorts=recommended_cohorts,
    )
    return SectionOption(
        option_id,
        course_id,
        (section,),
        requires_confirmation=requires_confirmation,
    )


def _course(course_id: str) -> Course:
    return Course(course_id, course_id.upper(), f"课程{course_id}", 2)


def test_solver_partially_schedules_by_priority() -> None:
    problem = SchedulingProblem(
        courses=(_course("high"), _course("medium"), _course("extra")),
        requests=(
            CourseRequest("high", priority=100),
            CourseRequest("medium", priority=80),
            CourseRequest("extra", priority=60),
        ),
        options=(
            _option("high-1", "high", weekday=1),
            _option("medium-1", "medium", weekday=1),
            _option("extra-1", "extra", weekday=2),
        ),
    )

    result = ScheduleSolver(SolverConfig(max_solutions=1)).solve(problem)

    assert result.status is SolveStatus.OPTIMAL
    assert result.plans[0].selected_option_ids == ("high-1", "extra-1")
    assert result.plans[0].unscheduled_course_ids == ("medium",)
    assert result.plans[0].coverage_score == 160
    assert validate_plan(problem, result.plans[0]).valid


def test_solver_respects_locked_teaching_class() -> None:
    preferred = _option("course-a", "course", weekday=1)
    locked = _option(
        "course-b",
        "course",
        weekday=2,
        instructor="尽量避开的教师",
    )
    problem = SchedulingProblem(
        courses=(_course("course"),),
        requests=(CourseRequest("course", priority=100, required=True),),
        options=(preferred, locked),
        constraints=UserConstraints(
            locked_option_ids=frozenset({"course-b"}),
            instructor_rules=(
                InstructorRule(
                    "avoid",
                    "尽量避开的教师",
                    strength=ConstraintStrength.SOFT,
                    penalty=50,
                ),
            ),
        ),
    )

    result = ScheduleSolver(SolverConfig(max_solutions=1)).solve(problem)

    assert result.plans[0].selected_option_ids == ("course-b",)
    assert result.plans[0].soft_penalty == 50
    assert "用户锁定" in " ".join(result.plans[0].explanations[0].messages)


def test_soft_teacher_rule_selects_zero_penalty_alternative() -> None:
    problem = SchedulingProblem(
        courses=(_course("course"),),
        requests=(CourseRequest("course", priority=100, required=True),),
        options=(
            _option(
                "disliked",
                "course",
                weekday=1,
                instructor="不想上的教师",
            ),
            _option("neutral", "course", weekday=2, instructor="其他教师"),
        ),
        constraints=UserConstraints(
            instructor_rules=(
                InstructorRule(
                    "avoid",
                    "不想上的教师",
                    strength=ConstraintStrength.SOFT,
                    penalty=100,
                ),
            )
        ),
    )

    result = ScheduleSolver(SolverConfig(max_solutions=1)).solve(problem)

    assert result.plans[0].selected_option_ids == ("neutral",)
    assert result.plans[0].soft_penalty == 0


@pytest.mark.parametrize("use_fallback", [False, True])
def test_administrative_class_is_a_high_priority_soft_preference(
    monkeypatch: pytest.MonkeyPatch,
    use_fallback: bool,
) -> None:
    if use_fallback:
        monkeypatch.setattr(solver_module, "cp_model", None)
    problem = SchedulingProblem(
        courses=(_course("course"),),
        requests=(CourseRequest("course", priority=100, required=True),),
        options=(
            _option(
                "other-class",
                "course",
                weekday=1,
                recommended_cohorts=("072243/072244",),
            ),
            _option(
                "my-class",
                "course",
                weekday=2,
                recommended_cohorts=("072241/072242",),
            ),
        ),
        constraints=UserConstraints(
            recommended_cohort="072242",
            non_recommended_cohort_penalty=200,
        ),
    )

    result = ScheduleSolver(SolverConfig(max_solutions=1)).solve(problem)

    assert result.plans[0].selected_option_ids == ("my-class",)
    assert result.plans[0].soft_penalty == 0


@pytest.mark.parametrize("use_fallback", [False, True])
def test_compact_days_preference_uses_fewer_teaching_days(
    monkeypatch: pytest.MonkeyPatch,
    use_fallback: bool,
) -> None:
    if use_fallback:
        monkeypatch.setattr(solver_module, "cp_model", None)
    problem = SchedulingProblem(
        courses=(_course("a"), _course("b")),
        requests=(
            CourseRequest("a", required=True),
            CourseRequest("b", required=True),
        ),
        options=(
            _option("a-monday", "a", weekday=1, periods=(1, 2)),
            _option("a-tuesday", "a", weekday=2, periods=(1, 2)),
            _option("b-tuesday", "b", weekday=2, periods=(3, 4)),
        ),
        constraints=UserConstraints(prefer_compact_days=True, compact_day_penalty=10),
    )

    result = ScheduleSolver(SolverConfig(max_solutions=1)).solve(problem)

    assert result.plans[0].selected_option_ids == ("a-tuesday", "b-tuesday")
    assert result.plans[0].soft_penalty == 10


def test_no_good_cuts_produce_at_least_two_distinct_plans() -> None:
    problem = SchedulingProblem(
        courses=(_course("a"), _course("b")),
        requests=(
            CourseRequest("a", priority=100, required=True),
            CourseRequest("b", priority=100, required=True),
        ),
        options=(
            _option("a-1", "a", weekday=1),
            _option("a-2", "a", weekday=2),
            _option("b-1", "b", weekday=3),
            _option("b-2", "b", weekday=4),
        ),
    )

    result = ScheduleSolver(SolverConfig(max_solutions=3)).solve(problem)

    selections = {plan.selected_option_ids for plan in result.plans}
    assert len(result.plans) >= 2
    assert len(selections) == len(result.plans)
    assert all(plan.coverage_score == 200 for plan in result.plans)
    assert all(validate_plan(problem, plan).valid for plan in result.plans)
    assert result.plan_limit == 3
    assert result.plans_truncated is True
    assert result.all_plans_returned is False


@pytest.mark.parametrize("use_fallback", [False, True])
def test_enumeration_returns_every_plan_when_count_is_below_limit(
    monkeypatch: pytest.MonkeyPatch,
    use_fallback: bool,
) -> None:
    if use_fallback:
        monkeypatch.setattr(solver_module, "cp_model", None)
    problem = SchedulingProblem(
        courses=(_course("course"),),
        requests=(CourseRequest("course", required=True),),
        options=tuple(
            _option(f"course-{index}", "course", weekday=index)
            for index in range(1, 4)
        ),
    )

    result = ScheduleSolver(SolverConfig(max_solutions=10)).solve(problem)

    assert len(result.plans) == 3
    assert result.plan_limit == 10
    assert result.all_plans_returned is True
    assert result.plans_truncated is False
    assert [plan.selected_option_ids for plan in result.plans] == [
        ("course-1",),
        ("course-2",),
        ("course-3",),
    ]


@pytest.mark.parametrize("use_fallback", [False, True])
@pytest.mark.parametrize("section_count", [10, 11])
def test_tenth_plan_boundary_is_probed_without_returning_the_eleventh(
    monkeypatch: pytest.MonkeyPatch,
    section_count: int,
    use_fallback: bool,
) -> None:
    if use_fallback:
        monkeypatch.setattr(solver_module, "cp_model", None)
    problem = SchedulingProblem(
        courses=(_course("course"),),
        requests=(CourseRequest("course", required=True),),
        options=tuple(
            _option(f"course-{index:02d}", "course", weekday=(index % 7) + 1)
            for index in range(section_count)
        ),
    )

    result = ScheduleSolver().solve(problem)

    assert len(result.plans) == 10
    assert result.plan_limit == 10
    assert result.all_plans_returned is (section_count == 10)
    assert result.plans_truncated is (section_count == 11)
    assert [plan.selected_option_ids for plan in result.plans] == sorted(
        plan.selected_option_ids for plan in result.plans
    )


def test_unknown_eleventh_probe_does_not_claim_exhaustion_or_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    option_ids = tuple(f"course-{index:02d}" for index in range(10))
    problem = SchedulingProblem(
        courses=(_course("course"),),
        requests=(CourseRequest("course", required=True),),
        options=tuple(
            _option(option_id, "course", weekday=(index % 7) + 1)
            for index, option_id in enumerate(option_ids)
        ),
    )
    state: dict[str, object] = {"first_pass_index": 0, "selected": ()}

    def fake_solve_pass(**kwargs: object) -> object:
        if kwargs["coverage_target"] is None:
            index = int(state["first_pass_index"])
            state["first_pass_index"] = index + 1
            if index == 10:
                return solver_module._PassResult(status=solver_module.cp_model.UNKNOWN)
            selected = (option_ids[index],)
            state["selected"] = selected
            return solver_module._PassResult(
                status=solver_module.cp_model.OPTIMAL,
                selected_option_ids=selected,
                coverage_score=100,
            )
        return solver_module._PassResult(
            status=solver_module.cp_model.OPTIMAL,
            selected_option_ids=state["selected"],
            coverage_score=100,
        )

    monkeypatch.setattr(solver_module, "_solve_pass", fake_solve_pass)

    result = ScheduleSolver().solve(problem)

    assert len(result.plans) == 10
    assert result.status is SolveStatus.FEASIBLE_TIMEOUT
    assert result.all_plans_returned is False
    assert result.plans_truncated is False


@pytest.mark.parametrize("use_fallback", [False, True])
def test_safe_exact_option_ranks_before_confirmed_unknown_time_options(
    monkeypatch: pytest.MonkeyPatch,
    use_fallback: bool,
) -> None:
    if use_fallback:
        monkeypatch.setattr(solver_module, "cp_model", None)
    unknown_ids = tuple(f"unknown-{index:02d}" for index in range(10))
    problem = SchedulingProblem(
        courses=(_course("course"),),
        requests=(CourseRequest("course", required=True),),
        options=(
            _option("safe-exact", "course", weekday=1),
            *(
                _option(
                    option_id,
                    "course",
                    weekday=1,
                    precision=TimePrecision.WEEK_ONLY,
                )
                for option_id in unknown_ids
            ),
        ),
        constraints=UserConstraints(
            confirmed_unknown_time_option_ids=frozenset(unknown_ids),
        ),
    )

    result = ScheduleSolver().solve(problem)

    assert len(result.plans) == 10
    assert result.plans[0].selected_option_ids == ("safe-exact",)
    assert result.plans[0].soft_penalty == 0
    assert all(plan.soft_penalty == 1_000 for plan in result.plans[1:])
    assert result.plans_truncated is True


@pytest.mark.parametrize("use_fallback", [False, True])
def test_reliable_option_ranks_before_confirmed_low_confidence_option(
    monkeypatch: pytest.MonkeyPatch,
    use_fallback: bool,
) -> None:
    if use_fallback:
        monkeypatch.setattr(solver_module, "cp_model", None)
    problem = SchedulingProblem(
        courses=(_course("course"),),
        requests=(CourseRequest("course", required=True),),
        options=(
            _option(
                "needs-confirmation",
                "course",
                weekday=1,
                requires_confirmation=True,
            ),
            _option("reliable", "course", weekday=2),
        ),
    )

    result = ScheduleSolver().solve(problem)

    assert [plan.selected_option_ids for plan in result.plans] == [
        ("reliable",),
        ("needs-confirmation",),
    ]
    assert [plan.soft_penalty for plan in result.plans] == [0, 500]
    assert result.all_plans_returned is True


@pytest.mark.parametrize("use_fallback", [False, True])
def test_exact_no_good_cut_does_not_lose_zero_priority_superset(
    monkeypatch: pytest.MonkeyPatch,
    use_fallback: bool,
) -> None:
    if use_fallback:
        monkeypatch.setattr(solver_module, "cp_model", None)
    problem = SchedulingProblem(
        courses=(_course("zero"),),
        requests=(CourseRequest("zero", priority=0, required=False),),
        options=(_option("zero-1", "zero", weekday=1),),
    )

    result = ScheduleSolver().solve(problem)

    assert [plan.selected_option_ids for plan in result.plans] == [(), ("zero-1",)]
    assert result.all_plans_returned is True
    assert result.plans_truncated is False


def test_diversity_distance_cannot_claim_full_enumeration() -> None:
    problem = SchedulingProblem(
        courses=(_course("zero"),),
        requests=(CourseRequest("zero", priority=0, required=False),),
        options=(_option("zero-1", "zero", weekday=1),),
    )

    result = ScheduleSolver(SolverConfig(min_option_difference=2)).solve(problem)

    assert len(result.plans) == 1
    assert result.all_plans_returned is False
    assert result.plans_truncated is False


def test_solver_limit_cannot_exceed_product_contract() -> None:
    with pytest.raises(ValueError, match="cannot exceed 10"):
        SolverConfig(max_solutions=11)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"all_plans_returned": True, "plans_truncated": True}, "both exhaustive"),
        ({"plans_truncated": True}, "must fill plan_limit"),
    ],
)
def test_solve_result_rejects_inconsistent_enumeration_metadata(
    overrides: dict[str, bool],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        SolveResult(status=SolveStatus.OPTIMAL, **overrides)


def test_fallback_no_good_cuts_are_deterministic_and_diverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(solver_module, "cp_model", None)
    problem = SchedulingProblem(
        courses=(_course("a"), _course("b")),
        requests=(
            CourseRequest("a", priority=100, required=True),
            CourseRequest("b", priority=100, required=True),
        ),
        options=(
            _option("a-1", "a", weekday=1),
            _option("a-2", "a", weekday=2),
            _option("b-1", "b", weekday=3),
            _option("b-2", "b", weekday=4),
        ),
    )
    config = SolverConfig(max_solutions=3, fallback_node_limit=1_000)

    first = ScheduleSolver(config).solve(problem)
    second = ScheduleSolver(config).solve(problem)

    assert first.plans == second.plans
    assert len(first.plans) >= 2
    assert len({plan.selected_option_ids for plan in first.plans}) == len(first.plans)
    assert all(validate_plan(problem, plan).valid for plan in first.plans)
    assert first.plans_truncated is True
    assert first.all_plans_returned is False


def test_infeasible_required_courses_receive_specific_diagnosis() -> None:
    problem = SchedulingProblem(
        courses=(_course("a"), _course("b")),
        requests=(
            CourseRequest("a", required=True),
            CourseRequest("b", required=True),
        ),
        options=(
            _option("a-1", "a", weekday=7, weeks=WeekMask.from_weeks([3])),
            _option("b-1", "b", weekday=7, weeks=WeekMask.from_weeks([3])),
        ),
    )

    result = ScheduleSolver().solve(problem)

    assert result.status is SolveStatus.INFEASIBLE
    assert result.diagnostics[0].code == "REQUIRED_COURSES_ALWAYS_CONFLICT"
    assert "所有候选教学班均冲突" in result.diagnostics[0].message


def test_independent_validator_rejects_a_conflicting_external_plan() -> None:
    problem = SchedulingProblem(
        courses=(_course("a"), _course("b")),
        requests=(CourseRequest("a"), CourseRequest("b")),
        options=(
            _option("a-1", "a", weekday=1),
            _option("b-1", "b", weekday=1),
        ),
    )
    invalid = SchedulePlan(
        selected_option_ids=("a-1", "b-1"),
        unscheduled_course_ids=(),
        coverage_score=200,
        soft_penalty=0,
    )

    report = validate_plan(problem, invalid)

    assert not report.valid
    assert any(issue.code == "SELECTED_OPTIONS_CONFLICT" for issue in report.issues)
