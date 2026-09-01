"""Generate a reproducible audit of imported CUG schedule snapshots."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .catalog_merge import MergedCatalog, MergeDisposition, merge_snapshots
from .cug_xls import import_schedule_files, import_schedule_zip
from .models import CatalogSnapshot, ImportedTeachingClass, TimePrecision


def build_audit_report(
    newest: CatalogSnapshot,
    legacy: CatalogSnapshot | None = None,
) -> dict[str, Any]:
    merged = merge_snapshots(newest, legacy) if legacy else merge_snapshots(newest)
    opt_in_merged = merge_snapshots(newest, legacy, include_old_only=True) if legacy else merged
    report: dict[str, Any] = {
        "schema_version": 1,
        "policy": {
            "name": "strategy_a_newest_snapshot_preferred",
            "same_teaching_class": (
                "Newest snapshot is selected; every source version remains attached for audit."
            ),
            "legacy_only": (
                "Retained with needs_confirmation=true and reliable_for_scheduling=false; "
                "excluded unless include_old_only is explicitly enabled."
            ),
            "selected_count": (
                "The source field is enrolled_count_snapshot, never capacity or availability."
            ),
        },
        "newest_snapshot": _snapshot_summary(newest),
        "merge": _merge_summary(merged, opt_in_merged),
        "audit_findings": _catalog_findings(newest),
    }
    if legacy:
        report["legacy_snapshot"] = _snapshot_summary(legacy)
        report["legacy_course_comparison"] = _compare_legacy_courses(newest, legacy)
    return report


def render_audit_markdown(report: dict[str, Any]) -> str:
    newest = report["newest_snapshot"]
    merge = report["merge"]
    findings = report["audit_findings"]
    old_only = merge["old_only_teaching_classes"]
    changed = merge["structurally_changed_teaching_classes"]
    course_comparison = report.get("legacy_course_comparison")
    lines = [
        "# 2026 秋课程总库导入与合并审计",
        "",
        "## 结论",
        "",
        "- 采用策略 A：同一教学班由最新快照覆盖，所有旧版本仍保留在 provenance 中。",
        "- 旧版独有教学班标记 `needs_confirmation=true`、"
        "`reliable_for_scheduling=false`，默认不进入排课候选。",
        "- 显式启用 `include_old_only` 后，仅时间完整的旧版独有教学班可成为候选；"
        "风险标记不会消失。",
        "- 原表的数字字段是已选人数快照，不是容量。本次导入没有任何非空容量值。",
        "- ZIP 条目只在内存中读取；路径穿越、绝对路径、符号链接、加密条目和异常压缩比均会被拒绝。",
        "",
        "## 最新快照",
        "",
        f"- 课程工作簿：{newest['course_workbooks']}",
        f"- 教学班组合键：{newest['teaching_classes']}",
        f"- 普通时段记录：{newest['exact_meetings']}",
        f"- 集中实践/周级记录：{newest['week_only_meetings']}",
        f"- 默认可靠教学班：{newest['reliable_teaching_classes']}",
        f"- 非空容量字段：{newest['non_null_capacity_fields']}",
        f"- 因 Windows 非法字符生成安全文件名：{findings['sanitized_filename_count']}",
        "",
        "## 合并结果",
        "",
        f"- 最新快照独有：{merge['statistics']['new_only']}",
        f"- 新旧均有且选用新版：{merge['statistics']['new_preferred']}",
        f"- 旧版独有：{merge['statistics']['old_only']}",
        f"- 结构变化：{merge['statistics']['structurally_changed']}",
        f"- 仅已选人数快照变化：{merge['statistics']['enrollment_only_changed']}",
        f"- 默认排课候选：{merge['statistics']['eligible_for_scheduling']}",
        f"- 显式接受旧版独有后的候选：{merge['eligible_with_old_only_opt_in']}",
        "",
    ]
    if course_comparison:
        counts = course_comparison["counts"]
        lines.extend(
            [
                "## 旧版课程工作簿对比",
                "",
                f"- 新版缺失：{counts['absent_from_newest']}",
                f"- 结构变化：{counts['structurally_changed']}",
                f"- 仅已选人数变化：{counts['enrollment_only_changed']}",
                f"- 无变化：{counts['unchanged']}",
                "",
            ]
        )
    lines.extend(["## 必须人工确认的旧版独有项", ""])
    if old_only:
        lines.extend(
            f"- `{item['course_code']}` {item['course_name']} / "
            f"{item['class_label']} / {item['class_composition']}"
            for item in old_only
        )
    else:
        lines.append("- 无")
    lines.extend(["", "## 结构变化项", ""])
    if changed:
        lines.extend(
            f"- `{item['course_code']}` {item['course_name']} / "
            f"{item['class_label']} / {item['class_composition']}"
            for item in changed
        )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 解析问题汇总",
            "",
            *(f"- `{code}`：{count}" for code, count in newest["issue_counts"].items()),
            "",
            "说明：集中实践只有周次、没有星期/节次/地点，因此即使来源是最新快照，"
            "也不会被伪装成无冲突的精确时段。",
            "",
        ]
    )
    return "\n".join(lines)


def write_audit_report(report: dict[str, Any], output_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "course_catalog_import_audit.json"
    markdown_path = directory / "course_catalog_import_audit.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_audit_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _snapshot_summary(snapshot: CatalogSnapshot) -> dict[str, Any]:
    sections = snapshot.teaching_classes
    meetings = tuple(meeting for section in sections for meeting in section.meetings)
    issue_counts: Counter[str] = Counter(issue.code for issue in snapshot.issues)
    issue_counts.update(issue.code for course in snapshot.courses for issue in course.issues)
    issue_counts.update(issue.code for section in sections for issue in section.issues)
    print_dates = sorted({course.print_date for course in snapshot.courses if course.print_date})
    return {
        "snapshot_id": snapshot.snapshot_id,
        "course_workbooks": len(snapshot.courses),
        "unique_course_codes": len({course.course_code.casefold() for course in snapshot.courses}),
        "teaching_classes": len(sections),
        "meetings": len(meetings),
        "exact_meetings": sum(
            meeting.precision is TimePrecision.EXACT_SLOT for meeting in meetings
        ),
        "week_only_meetings": sum(
            meeting.precision is TimePrecision.WEEK_ONLY for meeting in meetings
        ),
        "tbd_meetings": sum(meeting.precision is TimePrecision.TBD for meeting in meetings),
        "reliable_teaching_classes": sum(section.reliable_for_scheduling for section in sections),
        "needs_confirmation": sum(section.needs_confirmation for section in sections),
        "non_null_capacity_fields": sum(section.capacity is not None for section in sections),
        "enrolled_count_snapshot_fields": sum(
            section.enrolled_count_snapshot is not None for section in sections
        ),
        "print_dates": print_dates,
        "issue_counts": dict(sorted(issue_counts.items())),
    }


def _merge_summary(merged: MergedCatalog, opt_in: MergedCatalog) -> dict[str, Any]:
    names = {
        course.course_code.casefold(): course.selected_metadata.course_name
        for course in merged.courses
    }

    def describe(section: ImportedTeachingClass) -> dict[str, Any]:
        return {
            "course_code": section.course_code,
            "course_name": names.get(section.course_code.casefold(), ""),
            "section_code": section.section_code,
            "class_label": section.class_label_raw,
            "class_composition": section.class_composition_raw,
            "source_snapshot": section.source.snapshot_id,
            "source_name": section.source.original_entry_name or section.source.safe_filename,
            "enrolled_count_snapshot": section.enrolled_count_snapshot,
            "capacity": section.capacity,
        }

    old_only = []
    for item in merged.teaching_classes:
        if item.disposition is not MergeDisposition.OLD_ONLY:
            continue
        description = describe(item.selected)
        description.update(
            {
                "needs_confirmation": item.needs_confirmation,
                "reliable_for_scheduling": item.reliable_for_scheduling,
                "eligible_by_default": item.eligible_for_scheduling,
                "eligible_with_explicit_opt_in": item.selected.reliable_for_scheduling,
            }
        )
        old_only.append(description)
    structurally_changed = [
        describe(item.selected)
        for item in merged.teaching_classes
        if any(issue.code == "snapshot_structure_changed" for issue in item.issues)
    ]
    return {
        "statistics": {
            "new_only": merged.statistics.new_only,
            "new_preferred": merged.statistics.new_preferred,
            "old_only": merged.statistics.old_only,
            "structurally_changed": merged.statistics.structurally_changed,
            "enrollment_only_changed": merged.statistics.enrollment_only_changed,
            "eligible_for_scheduling": merged.statistics.eligible_for_scheduling,
        },
        "eligible_with_old_only_opt_in": opt_in.statistics.eligible_for_scheduling,
        "old_only_teaching_classes": old_only,
        "structurally_changed_teaching_classes": structurally_changed,
    }


def _catalog_findings(snapshot: CatalogSnapshot) -> dict[str, Any]:
    names: defaultdict[str, list[str]] = defaultdict(list)
    for course in snapshot.courses:
        names[course.course_name.strip()].append(course.course_code)
    duplicate_names = {
        name: sorted(codes)
        for name, codes in names.items()
        if len(set(code.casefold() for code in codes)) > 1
    }
    sanitized = [
        course.source.original_entry_name
        for course in snapshot.courses
        if course.source.original_entry_name
        and course.source.safe_filename
        != Path(course.source.original_entry_name.replace("\\", "/")).name
    ]
    section_code_collisions = 0
    by_course_and_code: defaultdict[tuple[str, str | None], set[str]] = defaultdict(set)
    for section in snapshot.teaching_classes:
        by_course_and_code[(section.course_code.casefold(), section.section_code)].add(
            section.class_composition_raw
        )
    for compositions in by_course_and_code.values():
        if len(compositions) > 1:
            section_code_collisions += 1
    return {
        "duplicate_trimmed_course_names": duplicate_names,
        "filename_course_names_with_trailing_space": sum(
            name is not None and name != name.rstrip()
            for course in snapshot.courses
            for name in (course.course_name_from_filename_raw,)
        ),
        "sanitized_filename_count": len(sanitized),
        "sanitized_original_names": sanitized,
        "course_section_codes_reused_across_compositions": section_code_collisions,
    }


def _compare_legacy_courses(newest: CatalogSnapshot, legacy: CatalogSnapshot) -> dict[str, Any]:
    new_by_code = {course.course_code.casefold(): course for course in newest.courses}
    categories: dict[str, list[dict[str, str]]] = {
        "absent_from_newest": [],
        "structurally_changed": [],
        "enrollment_only_changed": [],
        "unchanged": [],
    }
    for old_course in legacy.courses:
        description = {
            "course_code": old_course.course_code,
            "course_name": old_course.course_name,
            "source_name": (
                old_course.source.original_entry_name or old_course.source.safe_filename
            ),
        }
        new_course = new_by_code.get(old_course.course_code.casefold())
        if new_course is None:
            categories["absent_from_newest"].append(description)
            continue
        old_sections = {section.identity_key: section for section in old_course.teaching_classes}
        new_sections = {section.identity_key: section for section in new_course.teaching_classes}
        if old_sections.keys() != new_sections.keys() or any(
            _audit_structural_signature(old_sections[key])
            != _audit_structural_signature(new_sections[key])
            for key in old_sections.keys() & new_sections.keys()
        ):
            categories["structurally_changed"].append(description)
        elif any(
            old_sections[key].enrolled_count_snapshot != new_sections[key].enrolled_count_snapshot
            for key in old_sections
        ):
            categories["enrollment_only_changed"].append(description)
        else:
            categories["unchanged"].append(description)
    return {
        "counts": {name: len(items) for name, items in categories.items()},
        **categories,
    }


def _audit_structural_signature(section: ImportedTeachingClass) -> tuple[object, ...]:
    return (
        section.class_label_raw.strip(),
        section.section_code,
        tuple(sorted(section.class_composition, key=str.casefold)),
        tuple(sorted(section.instructors, key=str.casefold)),
        tuple(
            sorted(
                [
                    (
                        tuple(sorted(meeting.instructors, key=str.casefold)),
                        meeting.weeks,
                        meeting.weekday,
                        meeting.start_period,
                        meeting.end_period,
                        meeting.campus,
                        meeting.room,
                        meeting.precision,
                    )
                    for meeting in section.meetings
                ],
                key=repr,
            )
        ),
        section.assessment,
    )


def _expand_xls_paths(paths: list[Path]) -> tuple[Path, ...]:
    found: dict[str, Path] = {}
    for path in paths:
        candidates = path.rglob("*.xls") if path.is_dir() else (path,)
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.casefold() == ".xls":
                found[str(candidate.resolve()).casefold()] = candidate
    return tuple(sorted(found.values(), key=lambda item: str(item).casefold()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-zip", type=Path, required=True)
    parser.add_argument("--old", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()

    newest = import_schedule_zip(arguments.new_zip, snapshot_id="2026-08-23")
    old_files = _expand_xls_paths(arguments.old)
    legacy = import_schedule_files(old_files, snapshot_id="2026-08-12-to-13") if old_files else None
    report = build_audit_report(newest, legacy)
    json_path, markdown_path = write_audit_report(report, arguments.output_dir)
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
