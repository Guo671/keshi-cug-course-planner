"""Public scheduling service API."""

from .candidate import CandidateAudit, OptionEvaluation, evaluate_candidates
from .explain import build_plan_explanations, diagnose_infeasibility
from .fallback import FallbackSearchResult, deterministic_search
from .solver import (
    OrToolsUnavailableError,
    ScheduleSolver,
    SolverConfig,
    SolverInvariantError,
)
from .validation import (
    ValidationIssue,
    ValidationReport,
    validate_plan,
)

__all__ = [
    "CandidateAudit",
    "FallbackSearchResult",
    "OptionEvaluation",
    "OrToolsUnavailableError",
    "ScheduleSolver",
    "SolverConfig",
    "SolverInvariantError",
    "ValidationIssue",
    "ValidationReport",
    "build_plan_explanations",
    "diagnose_infeasibility",
    "deterministic_search",
    "evaluate_candidates",
    "validate_plan",
]
