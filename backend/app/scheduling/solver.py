"""Lexicographic CP-SAT schedule solver with diverse alternatives."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from itertools import combinations
from typing import Any

try:  # Keep domain and validation usable when the optional solver is absent.
    cp_model: Any = importlib.import_module("ortools.sat.python.cp_model")
except ImportError:  # pragma: no cover - exercised by monkeypatch in unit tests
    cp_model = None

from ..domain.conflicts import options_overlap
from ..domain.planning import (
    Diagnostic,
    SchedulePlan,
    SchedulingProblem,
    SolveResult,
    SolveStatus,
)
from .candidate import CandidateAudit, evaluate_candidates
from .explain import (
    build_plan_explanations,
    diagnose_infeasibility,
)
from .fallback import deterministic_search
from .scoring import plan_level_soft_penalty
from .validation import validate_plan


class OrToolsUnavailableError(RuntimeError):
    """Raised only when optimization is requested without OR-Tools installed."""


class SolverInvariantError(RuntimeError):
    """Raised if the independent validator rejects a generated plan."""


MAX_RETURNED_PLANS = 10


@dataclass(frozen=True, slots=True)
class SolverConfig:
    max_solutions: int = MAX_RETURNED_PLANS
    time_limit_seconds: float = 5.0
    random_seed: int = 0
    min_option_difference: int = 1
    allow_deterministic_fallback: bool = True
    fallback_node_limit: int = 250_000

    def __post_init__(self) -> None:
        if self.max_solutions < 1:
            raise ValueError("max_solutions must be at least 1")
        if self.max_solutions > MAX_RETURNED_PLANS:
            raise ValueError(f"max_solutions cannot exceed {MAX_RETURNED_PLANS}")
        if self.time_limit_seconds <= 0:
            raise ValueError("time_limit_seconds must be positive")
        if self.min_option_difference < 1:
            raise ValueError("min_option_difference must be at least 1")
        if self.fallback_node_limit < 1:
            raise ValueError("fallback_node_limit must be at least 1")


class ScheduleSolver:
    """Solve course coverage first, then preference penalties.

    Each alternative rebuilds the model with no-good cuts.  This avoids stale
    objective constraints and allows later alternatives to trade a lower
    priority course only when no equally covering distinct plan remains.
    """

    def __init__(self, config: SolverConfig | None = None) -> None:
        self.config = config or SolverConfig()

    def solve(self, problem: SchedulingProblem) -> SolveResult:
        audit = evaluate_candidates(problem)
        known_diagnostics = diagnose_infeasibility(problem, audit, include_generic=False)
        if known_diagnostics:
            return SolveResult(
                status=SolveStatus.INFEASIBLE,
                diagnostics=known_diagnostics,
                rejected_options=audit.rejected_options,
                plan_limit=self.config.max_solutions,
                all_plans_returned=True,
            )

        if cp_model is None:
            if not self.config.allow_deterministic_fallback:
                raise OrToolsUnavailableError(
                    "OR-Tools is unavailable and deterministic fallback was "
                    "disabled in SolverConfig. Install the project's declared "
                    "'ortools' dependency or enable the fallback."
                )
            return self._solve_with_fallback(problem, audit)

        plans: list[SchedulePlan] = []
        prior_selections: list[tuple[str, ...]] = []
        all_passes_optimal = True
        all_plans_returned = False
        plans_truncated = False

        # Solve one additional coverage pass after filling the response.  A
        # concrete extra incumbent proves truncation, while INFEASIBLE proves
        # that the returned list exhausted the remaining search space.  The
        # extra plan is deliberately not preference-optimized or serialized.
        for _ in range(self.config.max_solutions + 1):
            first_pass = _solve_pass(
                problem=problem,
                audit=audit,
                config=self.config,
                prior_selections=prior_selections,
                coverage_target=None,
            )
            if first_pass.status == cp_model.INFEASIBLE:
                all_plans_returned = (
                    not plans or self.config.min_option_difference == 1
                )
                if plans:
                    break
                return SolveResult(
                    status=SolveStatus.INFEASIBLE,
                    diagnostics=diagnose_infeasibility(problem, audit, include_generic=True),
                    rejected_options=audit.rejected_options,
                    plan_limit=self.config.max_solutions,
                    all_plans_returned=True,
                )
            if first_pass.status == cp_model.MODEL_INVALID:
                return _data_error_result(
                    audit,
                    "CP-SAT 模型校验失败",
                    plan_limit=self.config.max_solutions,
                )
            if first_pass.status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                if plans:
                    all_passes_optimal = False
                    break
                return SolveResult(
                    status=SolveStatus.UNKNOWN,
                    diagnostics=(
                        Diagnostic(
                            code="SOLVER_TIMEOUT_NO_SOLUTION",
                            message="求解在时限内未找到可行方案，也未证明无解",
                        ),
                    ),
                    rejected_options=audit.rejected_options,
                    plan_limit=self.config.max_solutions,
                )

            if len(plans) == self.config.max_solutions:
                probe_plan = _make_plan(problem, audit, first_pass.selected_option_ids)
                report = validate_plan(problem, probe_plan)
                if not report.valid:
                    details = "; ".join(issue.message for issue in report.issues)
                    raise SolverInvariantError(
                        f"independent truncation-probe validation failed: {details}"
                    )
                plans_truncated = True
                break

            all_passes_optimal &= first_pass.status == cp_model.OPTIMAL

            second_pass = _solve_pass(
                problem=problem,
                audit=audit,
                config=self.config,
                prior_selections=prior_selections,
                coverage_target=first_pass.coverage_score,
            )
            if second_pass.status == cp_model.MODEL_INVALID:
                return _data_error_result(
                    audit,
                    "CP-SAT 偏好优化模型校验失败",
                    plan_limit=self.config.max_solutions,
                )
            if second_pass.status == cp_model.INFEASIBLE:
                # The first-pass incumbent itself is feasible.  A failure after
                # fixing that exact score indicates a model/integration defect,
                # not a truthful user-facing infeasibility.
                raise SolverInvariantError(
                    "preference pass could not reproduce the coverage-pass score"
                )
            if second_pass.status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                all_passes_optimal &= second_pass.status == cp_model.OPTIMAL
                selected_ids = second_pass.selected_option_ids
            else:
                # A tiny time budget can expire before the preference pass
                # rediscovers a solution.  Preserve the independently valid
                # first-pass incumbent and report FEASIBLE_TIMEOUT.
                all_passes_optimal = False
                selected_ids = first_pass.selected_option_ids
            plan = _make_plan(problem, audit, selected_ids)
            report = validate_plan(problem, plan)
            if not report.valid:
                details = "; ".join(issue.message for issue in report.issues)
                raise SolverInvariantError(f"independent solution validation failed: {details}")
            plans.append(plan)
            prior_selections.append(selected_ids)
            if not selected_ids and not audit.accepted_options:
                # With no candidate variables, the empty selection is the sole
                # feasible arrangement and cannot be excluded by a no-good cut.
                all_plans_returned = True
                break

        status = SolveStatus.OPTIMAL if all_passes_optimal else SolveStatus.FEASIBLE_TIMEOUT
        return SolveResult(
            status=status,
            plans=_rank_plans(plans),
            rejected_options=audit.rejected_options,
            plan_limit=self.config.max_solutions,
            all_plans_returned=all_plans_returned,
            plans_truncated=plans_truncated,
        )

    def _solve_with_fallback(
        self, problem: SchedulingProblem, audit: CandidateAudit
    ) -> SolveResult:
        plans: list[SchedulePlan] = []
        prior_selections: list[tuple[str, ...]] = []
        all_searches_complete = True
        all_plans_returned = False
        plans_truncated = False

        for _ in range(self.config.max_solutions + 1):
            search = deterministic_search(
                problem,
                audit,
                prior_selections=prior_selections,
                min_option_difference=self.config.min_option_difference,
                node_limit=self.config.fallback_node_limit,
            )
            if search.selected_option_ids is None:
                all_plans_returned = search.search_complete and (
                    not plans or self.config.min_option_difference == 1
                )
                if plans:
                    all_searches_complete &= search.search_complete
                    break
                if search.search_complete:
                    return SolveResult(
                        status=SolveStatus.INFEASIBLE,
                        diagnostics=diagnose_infeasibility(problem, audit, include_generic=True),
                        rejected_options=audit.rejected_options,
                        plan_limit=self.config.max_solutions,
                        all_plans_returned=True,
                    )
                return SolveResult(
                    status=SolveStatus.UNKNOWN,
                    diagnostics=(
                        Diagnostic(
                            code="FALLBACK_LIMIT_NO_SOLUTION",
                            message=("确定性回退求解达到节点上限，尚未找到可行方案，也未证明无解"),
                        ),
                    ),
                    rejected_options=audit.rejected_options,
                    plan_limit=self.config.max_solutions,
                )

            if len(plans) == self.config.max_solutions:
                probe_plan = _make_plan(problem, audit, search.selected_option_ids)
                report = validate_plan(problem, probe_plan)
                if not report.valid:
                    details = "; ".join(issue.message for issue in report.issues)
                    raise SolverInvariantError(
                        f"fallback truncation-probe validation failed: {details}"
                    )
                plans_truncated = True
                break

            all_searches_complete &= search.search_complete
            plan = _make_plan(problem, audit, search.selected_option_ids)
            report = validate_plan(problem, plan)
            if not report.valid:
                details = "; ".join(issue.message for issue in report.issues)
                raise SolverInvariantError(f"fallback solution validation failed: {details}")
            plans.append(plan)
            prior_selections.append(search.selected_option_ids)
            if not search.selected_option_ids and not audit.accepted_options:
                all_plans_returned = search.search_complete
                break

        return SolveResult(
            status=(SolveStatus.OPTIMAL if all_searches_complete else SolveStatus.FEASIBLE_TIMEOUT),
            plans=_rank_plans(plans),
            rejected_options=audit.rejected_options,
            plan_limit=self.config.max_solutions,
            all_plans_returned=all_plans_returned,
            plans_truncated=plans_truncated,
        )


@dataclass(frozen=True, slots=True)
class _PassResult:
    status: int
    selected_option_ids: tuple[str, ...] = ()
    coverage_score: int = 0


def _solve_pass(
    *,
    problem: SchedulingProblem,
    audit: CandidateAudit,
    config: SolverConfig,
    prior_selections: list[tuple[str, ...]],
    coverage_target: int | None,
) -> _PassResult:
    model, variables, coverage_expression, penalty_expression = _build_model(
        problem=problem,
        audit=audit,
        prior_selections=prior_selections,
        min_option_difference=config.min_option_difference,
    )
    if coverage_target is None:
        model.Maximize(coverage_expression)
    else:
        model.Add(coverage_expression == coverage_target)
        model.Minimize(penalty_expression)

    cp_solver = cp_model.CpSolver()
    cp_solver.parameters.max_time_in_seconds = config.time_limit_seconds
    cp_solver.parameters.random_seed = config.random_seed
    cp_solver.parameters.num_search_workers = 1
    status = cp_solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return _PassResult(status=status)

    selected = tuple(
        option.id
        for option in audit.accepted_options
        if cp_solver.BooleanValue(variables[option.id])
    )
    coverage_score = sum(
        request.priority
        for request in problem.requests
        if any(
            option.course_id == request.course_id and option.id in selected
            for option in audit.accepted_options
        )
    )
    return _PassResult(
        status=status,
        selected_option_ids=selected,
        coverage_score=coverage_score,
    )


def _build_model(
    *,
    problem: SchedulingProblem,
    audit: CandidateAudit,
    prior_selections: list[tuple[str, ...]],
    min_option_difference: int,
) -> tuple[Any, dict[str, Any], Any, Any]:
    model = cp_model.CpModel()
    accepted = audit.accepted_options
    variables = {option.id: model.NewBoolVar(f"select__{option.id}") for option in accepted}
    options_by_course: dict[str, list[Any]] = {}
    for option in accepted:
        options_by_course.setdefault(option.course_id, []).append(variables[option.id])

    coverage_terms: list[Any] = []
    for request in problem.requests:
        course_variables = options_by_course.get(request.course_id, [])
        if course_variables:
            model.Add(sum(course_variables) <= 1)
            if request.required:
                model.Add(sum(course_variables) == 1)
            coverage_terms.extend(request.priority * variable for variable in course_variables)

    for locked_id in problem.constraints.locked_option_ids:
        model.Add(variables[locked_id] == 1)

    for left, right in combinations(accepted, 2):
        if left.course_id == right.course_id:
            # Already covered by the per-course at-most-one constraint.
            continue
        if options_overlap(left, right):
            model.Add(variables[left.id] + variables[right.id] <= 1)

    for selection in prior_selections:
        if not variables:
            continue
        selected_ids = set(selection)
        selected_variables = [
            variable for option_id, variable in variables.items() if option_id in selected_ids
        ]
        unselected_variables = [
            variable for option_id, variable in variables.items() if option_id not in selected_ids
        ]
        required_difference = min_option_difference
        # Exact-set no-good cut.  Counting both removed and newly added options
        # is essential for zero-priority courses: the old one-sided cut also
        # rejected every superset and could falsely claim enumeration complete.
        model.Add(
            sum(selected_variables) - sum(unselected_variables)
            <= len(selected_variables) - required_difference
        )

    evaluation_by_id = audit.by_id()
    penalty_terms = [
        evaluation_by_id[option.id].soft_penalty * variables[option.id]
        for option in accepted
        if evaluation_by_id[option.id].soft_penalty
    ]
    if problem.constraints.prefer_compact_days:
        for weekday in range(1, 8):
            day_option_variables = [
                variables[option.id]
                for option in accepted
                if any(
                    meeting.is_exact and meeting.weekday == weekday
                    for meeting in option.meetings
                )
            ]
            if not day_option_variables:
                continue
            day_used = model.NewBoolVar(f"teaching_day__{weekday}")
            for option_variable in day_option_variables:
                model.Add(day_used >= option_variable)
            model.Add(day_used <= sum(day_option_variables))
            penalty_terms.append(problem.constraints.compact_day_penalty * day_used)
    coverage_expression = sum(coverage_terms)
    penalty_expression = sum(penalty_terms)
    return model, variables, coverage_expression, penalty_expression


def _make_plan(
    problem: SchedulingProblem,
    audit: CandidateAudit,
    selected_ids: tuple[str, ...],
) -> SchedulePlan:
    selected_set = set(selected_ids)
    selected_course_ids = {
        option.course_id for option in audit.accepted_options if option.id in selected_set
    }
    request_by_course = {request.course_id: request for request in problem.requests}
    coverage_score = sum(request_by_course[course_id].priority for course_id in selected_course_ids)
    evaluation_by_id = audit.by_id()
    selected_options = [
        option for option in audit.accepted_options if option.id in selected_set
    ]
    soft_penalty = sum(
        evaluation_by_id[option_id].soft_penalty for option_id in selected_ids
    ) + plan_level_soft_penalty(problem, selected_options)
    unscheduled = tuple(
        request.course_id
        for request in problem.requests
        if request.course_id not in selected_course_ids
    )
    return SchedulePlan(
        selected_option_ids=selected_ids,
        unscheduled_course_ids=unscheduled,
        coverage_score=coverage_score,
        soft_penalty=soft_penalty,
        explanations=build_plan_explanations(problem, audit, selected_ids),
    )


def _rank_plans(plans: list[SchedulePlan]) -> tuple[SchedulePlan, ...]:
    """Return recommendation order with deterministic tie-breaking."""

    return tuple(
        sorted(
            plans,
            key=lambda plan: (
                -plan.coverage_score,
                plan.soft_penalty,
                plan.selected_option_ids,
            ),
        )
    )


def _data_error_result(
    audit: CandidateAudit,
    message: str,
    *,
    plan_limit: int,
) -> SolveResult:
    return SolveResult(
        status=SolveStatus.DATA_ERROR,
        diagnostics=(Diagnostic(code="SOLVER_MODEL_INVALID", message=message),),
        rejected_options=audit.rejected_options,
        plan_limit=plan_limit,
    )
