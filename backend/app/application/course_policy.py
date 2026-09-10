"""Explicit product rule for untimed practical work; preserve original source times."""

from typing import Any

from ..infrastructure.tables import CatalogSection

UNKNOWN_PRECISIONS = {"week_only", "date_range", "tbd"}
PRACTICE_ISSUES = {
    "practice_time_unknown",
    "non_numeric_practice_identity",
    "practice_head_unparsed",
    "week_expression_invalid",
    "practice_duration_mismatch",
}


def effective_precision(raw: dict[str, Any], issues: list[dict[str, Any]] | None = None) -> str:
    value = str(raw.get("precision", "exact_slot"))
    if raw.get("non_blocking") or value == "non_blocking":
        return "non_blocking"
    notes = [*(issues or []), *raw.get("issues", [])]
    if value in UNKNOWN_PRECISIONS and any(i.get("code") == "practice_time_unknown" for i in notes):
        return "non_blocking"
    return value


def has_non_blocking_time(section: CatalogSection) -> bool:
    return any(
        effective_precision(m, section.import_issues) == "non_blocking" for m in section.meetings
    )


def needs_confirmation(section: CatalogSection) -> bool:
    if not section.needs_confirmation or not has_non_blocking_time(section):
        return section.needs_confirmation
    return any(
        i.get("code") not in PRACTICE_ISSUES
        and (i.get("severity") == "error" or i.get("code") == "old_snapshot_only")
        for i in section.import_issues
    )
