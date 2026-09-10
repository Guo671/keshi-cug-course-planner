"""Minimum concrete attendance changes; never relabel these as conflict-free plans."""

from __future__ import annotations

from collections import defaultdict
from time import monotonic
from typing import Any

from ..domain.planning import SchedulingProblem
from .candidate import evaluate_candidates
from .resources import size_limit_message
from .solver import cp_model


def suggest_adjustment(problem: SchedulingProblem) -> dict[str, Any]:
    size_error = size_limit_message(problem)
    if size_error:
        return {"kind": "unavailable", "message": size_error}
    deadline = monotonic() + 8
    if cp_model is None:
        return {"kind": "unavailable", "message": "请假建议需要 OR-Tools；普通排课仍可使用。"}
    audit = evaluate_candidates(problem)
    options = [o for o in audit.accepted_options if not o.has_unknown_time]
    grouped: dict[str, list[Any]] = defaultdict(list)
    locked = problem.constraints.locked_option_ids
    locked_courses = {o.course_id for o in problem.options if o.id in locked}
    for o in options:
        if o.course_id in locked_courses and o.id not in locked:
            continue
        grouped[o.course_id].append(o)
    courses = {c.id: c for c in problem.courses}

    class AdjustmentTimeout(Exception):
        pass

    def build(allow_drop: bool) -> tuple[Any, dict[str, Any], list[Any], dict[str, Any]]:
        model = cp_model.CpModel()
        selected = {
            o.id: model.new_bool_var(f"option:{o.id}") for os in grouped.values() for o in os
        }
        dropped = {}
        for r in problem.requests:
            xs = [selected[o.id] for o in grouped[r.course_id]]
            d = model.new_bool_var(f"drop:{r.course_id}")
            dropped[r.course_id] = d
            model.add(sum(xs) + d == 1)
            if not allow_drop:
                model.add(d == 0)
        occurrences: list[Any] = []
        occupancy: dict[tuple[int, int, int], list[Any]] = defaultdict(list)
        for os in grouped.values():
            for o in os:
                if monotonic() >= deadline:
                    raise AdjustmentTimeout
                intervals: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
                for m in o.meetings:
                    if m.is_exact:
                        for w in m.weeks.weeks:
                            intervals[(w, m.weekday)].append((m.start_period, m.end_period))
                # Overlapping and adjacent records of one teaching session count once.
                for (w, day), spans in sorted(intervals.items()):
                    merged: list[list[int]] = []
                    for start, end in sorted(spans):
                        if merged and start <= merged[-1][1] + 1:
                            merged[-1][1] = max(merged[-1][1], end)
                        else:
                            merged.append([start, end])
                    for start, end in merged:
                        leave = model.new_bool_var(f"leave:{len(occurrences)}")
                        attend = model.new_bool_var(f"attend:{len(occurrences)}")
                        model.add(attend + leave == selected[o.id])
                        record = {
                            "course_id": o.course_id,
                            "course_name": courses[o.course_id].name,
                            "option_id": o.id,
                            "section_code": "+".join(s.section_code for s in o.sections),
                            "week": w,
                            "weekday": day,
                            "start_period": start,
                            "end_period": end,
                        }
                        occurrences.append((leave, attend, record))
                        for period in range(start, end + 1):
                            occupancy[(w, day, period)].append(attend)
        for variables in occupancy.values():
            if len(variables) > 1:
                model.add(sum(variables) <= 1)
        leaves = sum(item[0] for item in occurrences)
        if allow_drop:
            model.add(leaves <= 10)
            # Each dropped course costs more than all permitted leaves combined.
            model.minimize(11 * sum(dropped.values()) + leaves)
        else:
            model.minimize(leaves)
        return model, selected, occurrences, dropped

    try:
        model, selected, occurrences, dropped = build(False)
    except AdjustmentTimeout:
        return {"kind": "unknown", "message": "构建调整建议超时，请减少候选教学班后重试。"}
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.001, deadline - monotonic())
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE, cp_model.INFEASIBLE):
        return {
            "kind": "unknown",
            "message": "时限内未找到可核验的调整建议，请减少候选教学班后重试。",
        }
    count = (
        sum(solver.value(v) for v, _, _ in occurrences) if status != cp_model.INFEASIBLE else None
    )
    full_minimum = count if status == cp_model.OPTIMAL else None
    allow_drop = status == cp_model.INFEASIBLE or (count is not None and count > 10)
    if allow_drop:
        try:
            model, selected, occurrences, dropped = build(True)
        except AdjustmentTimeout:
            return {"kind": "unknown", "message": "调整建议达到时限，请减少候选教学班后重试。"}
        solver.parameters.max_time_in_seconds = max(0.001, deadline - monotonic())
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {"kind": "unknown", "message": "未能在时限内生成删课建议。"}
    leaves = [record for var, _, record in occurrences if solver.value(var)]
    removals = [
        {"course_id": cid, "course_name": courses[cid].name}
        for cid, var in dropped.items()
        if solver.value(var)
    ]
    # Independent slot validation of every retained attendance occurrence.
    occupied: set[tuple[int, int, int]] = set()
    for _, attend, record in occurrences:
        if not solver.value(attend):
            continue
        slots = {
            (record["week"], record["weekday"], p)
            for p in range(record["start_period"], record["end_period"] + 1)
        }
        if occupied.intersection(slots):
            raise RuntimeError("adjustment attendance validation failed")
        occupied.update(slots)
    return {
        "kind": "drop_courses" if removals else "leave",
        "optimal": status == cp_model.OPTIMAL,
        "full_schedule_minimum_leave_count": full_minimum,
        "leave_count": len(leaves),
        "leaves": leaves,
        "remove_courses": removals,
        "selected_option_ids": [oid for oid, var in selected.items() if solver.value(var)],
        "selected_courses": [
            {
                "course_name": courses[o.course_id].name,
                "section_code": "+".join(s.section_code for s in o.sections),
                "instructors": list(o.instructors),
            }
            for os in grouped.values()
            for o in os
            if solver.value(selected[o.id])
        ],
        "message": (
            "这是需要向任课教师申请并获准的调整建议，尚不是无冲突课表。"
            "已保留教师排除、禁排时间和教学班限制；时段未知的课需先补齐时间。"
        ),
    }
