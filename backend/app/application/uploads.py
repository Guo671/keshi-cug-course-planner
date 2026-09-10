"""Batch upload parsing. All files form ONE snapshot, never legacy supplements."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ..api.schemas import CourseChoice, CustomCourse, CustomMeeting, CustomSection
from ..domain.section_identity import needs_component_review
from ..importers import CatalogSnapshot, MergedCatalog, import_schedule_zip, merge_snapshots
from ..importers.cug_xls import WorkbookImportError, parse_workbook_bytes
from ..importers.models import ImportedCourseSchedule, ImportIssue, SourceDocument


def parse_uploads(
    files: list[tuple[str, bytes]], *, archives: bool = False
) -> tuple[MergedCatalog, int]:
    maximum = 5 if archives else 20
    if not 1 <= len(files) <= maximum:
        raise WorkbookImportError(f"一次请选择 1–{maximum} 个文件")
    snapshot_id = "upload-" + uuid4().hex
    courses: list[ImportedCourseSchedule] = []
    issues: list[ImportIssue] = []
    with TemporaryDirectory(prefix="keshi-upload-") as directory:
        for index, (name, data) in enumerate(files):
            suffix = Path(name).suffix.lower()
            if archives:
                if suffix != ".zip":
                    raise WorkbookImportError("课程总库批量导入只接受 ZIP")
                path = Path(directory) / f"{index}.zip"
                path.write_bytes(data)
                snapshot = import_schedule_zip(path, snapshot_id=snapshot_id, strict=True)
                errors = [i for i in snapshot.issues if i.severity.value == "error"]
                if errors:
                    raise WorkbookImportError("；".join(i.message for i in errors[:5]))
                if not snapshot.courses:
                    raise WorkbookImportError(f"{name} 中没有可读取的课程表格")
                issues.extend(snapshot.issues)
                for course in snapshot.courses:
                    source = replace(course.source, container=name)
                    courses.append(
                        replace(
                            course,
                            source=source,
                            teaching_classes=tuple(
                                replace(
                                    section,
                                    source=source,
                                    meetings=tuple(
                                        replace(meeting, source=source)
                                        for meeting in section.meetings
                                    ),
                                )
                                for section in course.teaching_classes
                            ),
                        )
                    )
            else:
                if suffix not in (".xls", ".xlsx"):
                    raise WorkbookImportError("请选择 XLS 或 XLSX 课程课表")
                source = SourceDocument(
                    snapshot_id=snapshot_id,
                    kind="file",
                    container=name,
                    original_entry_name=name,
                    safe_filename=f"{index}{suffix}",
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                )
                try:
                    courses.append(parse_workbook_bytes(data, source=source))
                except WorkbookImportError as exc:
                    raise WorkbookImportError(f"{name}：{exc}") from exc
    terms = {c.term for c in courses if c.term}
    if len(terms) > 1:
        raise WorkbookImportError("文件包含多个学期，请按同一学期重新选择")
    courses.sort(
        key=lambda c: (c.print_date or "", c.export_token or "", c.source.sha256), reverse=True
    )
    merged = merge_snapshots(CatalogSnapshot(snapshot_id, tuple(courses), tuple(issues)))
    if not merged.courses:
        raise WorkbookImportError("没有读到课程，原总库未修改")
    return merged, len(courses)


def direct_choices(catalog: MergedCatalog) -> list[CourseChoice]:
    choices = []
    for course in catalog.courses:
        cid = "custom:" + uuid4().hex
        sections = []
        for index, merged_section in enumerate(course.teaching_classes):
            section = merged_section.selected
            if not section.meetings or any(
                not (
                    m.reliable_for_scheduling
                    or m.precision.value == "non_blocking"
                    or any(i.code == "practice_time_unknown" for i in m.issues)
                )
                for m in section.meetings
            ):
                raise WorkbookImportError(
                    f"{course.selected_metadata.course_name} 的教学班 {section.class_label_raw} "
                    "含未确认或缺失时段；请使用总库导入后核对，或手动补齐时间"
                )
            sections.append(
                CustomSection(
                    id=str(index),
                    section_code=section.section_code or section.class_label_raw,
                    instructors=list(section.instructors),
                    meetings=[
                        CustomMeeting(
                            non_blocking=m.precision.value == "non_blocking"
                            or any(i.code == "practice_time_unknown" for i in m.issues),
                            weeks=list(m.weeks),
                            weekday=m.weekday,
                            start_period=m.start_period,
                            end_period=m.end_period,
                            campus=m.campus,
                            room=m.room,
                        )
                        for m in section.meetings
                    ],
                )
            )
        choices.append(
            CourseChoice(
                course_id=cid,
                custom=CustomCourse(
                    component_relationship_confirmed=not needs_component_review(
                        s.section_code for s in sections
                    ),
                    name=course.selected_metadata.course_name,
                    code=course.course_code,
                    sections=sections,
                ),
            )
        )
    return choices
