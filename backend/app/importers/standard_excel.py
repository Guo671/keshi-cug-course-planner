"""A documented, stable table format independent of the school's print layout."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from .models import (
    CellReference,
    Confidence,
    ImportedCourseSchedule,
    ImportedMeeting,
    SourceDocument,
    TimePrecision,
)

STANDARD_HEADERS = [
    "课程号",
    "课程名称",
    "教学班",
    "教师",
    "周次",
    "星期",
    "开始节次",
    "结束节次",
    "校区",
    "地点",
    "时间类型",
]


def is_standard_table(rows: Sequence[Sequence[Any]]) -> bool:
    return bool(rows) and {"课程名称", "周次", "星期", "开始节次", "结束节次"}.issubset(
        {str(v or "").strip() for v in rows[0]}
    )


def parse_standard_table(
    rows: Sequence[Sequence[Any]], source: SourceDocument, sheet_name: str
) -> ImportedCourseSchedule:
    from .cug_xls import WorkbookImportError, _group_teaching_classes, parse_week_expression

    columns = {str(v or "").strip(): i for i, v in enumerate(rows[0])}
    headers = [str(v).strip() for v in rows[0] if str(v or "").strip()]
    if len(set(headers)) != len(headers):
        raise WorkbookImportError("标准模板存在重复列名，请保留每列一次")
    meetings = []
    identities = set()
    for line, row in enumerate(rows[1:], 2):
        if not any(str(v or "").strip() for v in row):
            continue

        def get(key: str, row: Sequence[Any] = row) -> str:
            index = columns.get(key)
            if index is None or index >= len(row):
                return ""
            value = row[index]
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value or "").strip()

        name = get("课程名称")
        if not name:
            raise WorkbookImportError(f"第 {line} 行缺少课程名称")
        code = get("课程号") or "自填-" + hashlib.sha256(name.encode()).hexdigest()[:12]
        identities.add((code, name))
        if len(identities) > 1:
            raise WorkbookImportError("标准模板每个文件填写一门课程，可包含多个教学班和时段")
        non_blocking = get("时间类型").casefold() in {
            "不占时段",
            "不占用时段",
            "实践",
            "实习",
            "课程设计",
            "non_blocking",
        }
        raw_weeks = get("周次").replace("第", "").replace("（", "(").replace("）", ")")
        parts = re.split(r"[,，、;；]", raw_weeks)
        week_expression = ",".join(p.strip() if "周" in p else p.strip() + "周" for p in parts)
        parsed = parse_week_expression(week_expression, total_weeks=64)
        weeks = parsed.weeks
        if non_blocking and raw_weeks in {"", "待定", "未定"}:
            weeks = ()
        elif parsed.issues or not parsed.weeks:
            raise WorkbookImportError(f"第 {line} 行周次无法识别：{raw_weeks}")
        weekday: int | None = None
        start: int | None = None
        end: int | None = None
        if not non_blocking:
            day = get("星期").replace("星期", "").replace("周", "")
            weekday = (
                int(day)
                if day.isdigit()
                else {c: i for i, c in enumerate("一二三四五六日", 1)}.get(
                    day, 7 if day == "天" else 0
                )
            )
            try:
                start, end = int(get("开始节次")), int(get("结束节次"))
            except ValueError as exc:
                raise WorkbookImportError(f"第 {line} 行须填写整数节次") from exc
            if not 1 <= weekday <= 7 or not 1 <= start <= end <= 20:
                raise WorkbookImportError(f"第 {line} 行星期或节次超出范围")
        section = get("教学班") or "0001"
        instructors = tuple(x.strip() for x in re.split(r"[,，、;；]", get("教师")) if x.strip())
        meetings.append(
            ImportedMeeting(
                raw=" | ".join(str(v or "") for v in row),
                source=source,
                cell=CellReference(sheet=sheet_name, row=line, column=1),
                instructors_raw=get("教师"),
                instructors=instructors,
                week_expression_raw=raw_weeks,
                weeks=weeks,
                weekday=weekday,
                start_period=start,
                end_period=end,
                campus=get("校区") or None,
                room=get("地点") or None,
                location_raw=get("地点") or None,
                class_label_raw=f"{name}-{section}",
                section_code=section,
                section_base_code=None,
                section_suffix=None,
                class_composition_raw="",
                class_composition=(),
                enrolled_count_snapshot=None,
                assessment=None,
                precision=TimePrecision.NON_BLOCKING if non_blocking else TimePrecision.EXACT_SLOT,
                confidence=Confidence.HIGH,
            )
        )
    if not meetings:
        raise WorkbookImportError("模板没有填写课程时间")
    code, name = next(iter(identities))
    return ImportedCourseSchedule(
        course_code=code,
        course_name=name,
        course_name_from_filename_raw=None,
        course_name_from_title_raw=name,
        offering_college=None,
        term=None,
        term_start=None,
        term_end=None,
        total_weeks=64,
        print_date=None,
        export_token=None,
        source=source,
        teaching_classes=_group_teaching_classes(code, meetings, source),
    )
