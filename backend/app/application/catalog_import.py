"""Persist the lossless importer/merge result into the query database."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..importers import MergedCatalog, MergeDisposition
from ..importers.models import (
    Confidence,
    ImportedCourseSchedule,
    ImportedMeeting,
    ImportedTeachingClass,
    ImportIssue,
    SourceDocument,
)
from ..infrastructure.tables import (
    CatalogCourse,
    CatalogSection,
)
from ..infrastructure.tables import (
    CatalogSnapshot as CatalogSnapshotRow,
)


@dataclass(frozen=True, slots=True)
class CatalogPersistenceSummary:
    snapshots: int
    courses: int
    sections: int
    default_eligible_sections: int
    confirmation_required_sections: int
    exact_meetings: int
    unknown_time_meetings: int


def replace_persisted_catalog(
    db: Session,
    catalog: MergedCatalog,
) -> CatalogPersistenceSummary:
    """Atomically replace only catalog-owned tables with a merged snapshot."""

    db.execute(delete(CatalogSection))
    db.execute(delete(CatalogCourse))
    db.execute(delete(CatalogSnapshotRow))

    snapshot_rows = _build_snapshot_rows(catalog)
    db.add_all(snapshot_rows)
    courses = 0
    sections = 0
    default_eligible = 0
    confirmation_required = 0
    exact_meetings = 0
    unknown_meetings = 0

    for merged_course in catalog.courses:
        metadata = merged_course.selected_metadata
        course_id = stable_course_id(merged_course.course_code)
        aliases = list(
            dict.fromkeys(
                version.course_name
                for version in merged_course.metadata_versions
                if version.course_name and version.course_name != metadata.course_name
            )
        )
        db.add(
            CatalogCourse(
                id=course_id,
                code=merged_course.course_code,
                name=metadata.course_name,
                credits=None,
                aliases=aliases,
                metadata_json={
                    "offering_college": metadata.offering_college,
                    "term": metadata.term,
                    "term_start": metadata.term_start,
                    "term_end": metadata.term_end,
                    "total_weeks": metadata.total_weeks,
                    "print_date": metadata.print_date,
                    "metadata_versions": [
                        _course_metadata_provenance(item)
                        for item in merged_course.metadata_versions
                    ],
                },
            )
        )
        courses += 1
        for merged_section in merged_course.teaching_classes:
            selected = merged_section.selected
            section_id = stable_section_id(selected)
            meeting_payloads = [_meeting_payload(item) for item in selected.meetings]
            exact_meetings += sum(item["precision"] == "exact_slot" for item in meeting_payloads)
            unknown_meetings += sum(item["precision"] != "exact_slot" for item in meeting_payloads)
            section_issues = [
                *_serialize_issues(merged_section.issues),
                *_serialize_issues(selected.issues),
            ]
            # The code is deliberately duplicated into persisted issues so
            # application logic can distinguish old-only evidence from a
            # current concentrated-practice record that merely lacks a slot.
            if merged_section.disposition is MergeDisposition.OLD_ONLY and not any(
                item.get("code") == "old_snapshot_only" for item in section_issues
            ):
                section_issues.append(
                    {
                        "code": "old_snapshot_only",
                        "message": "最新版快照中未找到；缺失不等于取消，必须到教务系统确认",
                        "severity": "warning",
                        "cell": None,
                    }
                )
            needs_confirmation = merged_section.needs_confirmation or (
                merged_section.disposition is MergeDisposition.OLD_ONLY
            )
            db.add(
                CatalogSection(
                    id=section_id,
                    course_id=course_id,
                    section_code=selected.section_code or selected.class_label_raw,
                    display_name=selected.class_label_raw,
                    instructors=list(selected.instructors),
                    meetings=meeting_payloads,
                    composition=list(selected.class_composition),
                    assessment=selected.assessment,
                    enrolled_count=selected.enrolled_count_snapshot,
                    capacity=None,
                    source_snapshot_id=selected.source.snapshot_id,
                    source_rank=_snapshot_rank(catalog, selected.source.snapshot_id),
                    needs_confirmation=needs_confirmation,
                    default_eligible=merged_section.eligible_for_scheduling,
                    parse_confidence=_confidence_number(selected.confidence),
                    provenance=[
                        _section_provenance(version) for version in merged_section.versions
                    ],
                    import_issues=section_issues,
                )
            )
            sections += 1
            default_eligible += int(merged_section.eligible_for_scheduling)
            confirmation_required += int(needs_confirmation)

    db.flush()
    return CatalogPersistenceSummary(
        snapshots=len(snapshot_rows),
        courses=courses,
        sections=sections,
        default_eligible_sections=default_eligible,
        confirmation_required_sections=confirmation_required,
        exact_meetings=exact_meetings,
        unknown_time_meetings=unknown_meetings,
    )


def stable_course_id(course_code: str) -> str:
    normalized = "".join(course_code.split()).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"course:{digest}"


def stable_section_id(section: ImportedTeachingClass) -> str:
    serialized = json.dumps(section.identity_key, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:28]
    return f"section:{digest}"


def _build_snapshot_rows(catalog: MergedCatalog) -> list[CatalogSnapshotRow]:
    sources: dict[str, list[SourceDocument]] = defaultdict(list)
    print_dates: dict[str, list[str]] = defaultdict(list)
    for course in catalog.courses:
        for metadata in course.metadata_versions:
            sources[metadata.source.snapshot_id].append(metadata.source)
            if metadata.print_date:
                print_dates[metadata.source.snapshot_id].append(metadata.print_date)
        for section in course.teaching_classes:
            for version in section.versions:
                sources[version.source.snapshot_id].append(version.source)

    rows: list[CatalogSnapshotRow] = []
    ordered_ids = (catalog.newest_snapshot_id, *catalog.legacy_snapshot_ids)
    for snapshot_id in ordered_ids:
        unique_sources = {
            (source.sha256, source.container, source.original_entry_name): source
            for source in sources.get(snapshot_id, [])
        }
        source_values = list(unique_sources.values())
        aggregate_hash = hashlib.sha256(
            "\n".join(sorted(source.sha256 for source in source_values)).encode("ascii")
        ).hexdigest()
        containers = list(dict.fromkeys(source.container for source in source_values))
        rows.append(
            CatalogSnapshotRow(
                id=snapshot_id,
                label=(
                    f"最新版课程总库 {snapshot_id}"
                    if snapshot_id == catalog.newest_snapshot_id
                    else f"旧版补充快照 {snapshot_id}"
                ),
                captured_at=_captured_at(print_dates.get(snapshot_id, [])),
                source_path="; ".join(containers),
                source_sha256=aggregate_hash,
                source_rank=_snapshot_rank(catalog, snapshot_id),
                is_primary=snapshot_id == catalog.newest_snapshot_id,
                metadata_json={
                    "document_count": len(source_values),
                    "source_kinds": sorted({source.kind for source in source_values}),
                },
            )
        )
    return rows


def _snapshot_rank(catalog: MergedCatalog, snapshot_id: str) -> int:
    if snapshot_id == catalog.newest_snapshot_id:
        return 1000
    try:
        return 900 - catalog.legacy_snapshot_ids.index(snapshot_id)
    except ValueError:
        return 0


def _captured_at(values: list[str]) -> datetime | None:
    for value in sorted(set(values), reverse=True):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


def _meeting_payload(meeting: ImportedMeeting) -> dict[str, Any]:
    return {
        "weeks": list(meeting.weeks),
        "weekday": meeting.weekday,
        "start_period": meeting.start_period,
        "end_period": meeting.end_period,
        "campus": meeting.campus,
        "room": meeting.room,
        "precision": meeting.precision.value,
        "source_ref": (
            f"{meeting.source.snapshot_id}:{meeting.source.safe_filename}:"
            f"{meeting.cell.sheet}!{meeting.cell.a1}"
        ),
        "raw": meeting.raw,
        "week_expression_raw": meeting.week_expression_raw,
        "confidence": meeting.confidence.value,
        "issues": _serialize_issues(meeting.issues),
    }


def _section_provenance(section: ImportedTeachingClass) -> dict[str, Any]:
    return {
        "snapshot_id": section.source.snapshot_id,
        "source": _source_payload(section.source),
        "class_label_raw": section.class_label_raw,
        "section_code": section.section_code,
        "class_composition_raw": section.class_composition_raw,
        "instructors": list(section.instructors),
        "enrolled_count_snapshot": section.enrolled_count_snapshot,
        "capacity": None,
        "assessment": section.assessment,
        "confidence": section.confidence.value,
        "reliable_for_scheduling": section.reliable_for_scheduling,
        "needs_confirmation": section.needs_confirmation,
        "meetings": [_meeting_payload(meeting) for meeting in section.meetings],
        "issues": _serialize_issues(section.issues),
    }


def _course_metadata_provenance(course: ImportedCourseSchedule) -> dict[str, Any]:
    return {
        "snapshot_id": course.source.snapshot_id,
        "course_code": course.course_code,
        "course_name": course.course_name,
        "course_name_from_filename_raw": course.course_name_from_filename_raw,
        "course_name_from_title_raw": course.course_name_from_title_raw,
        "source": _source_payload(course.source),
        "issues": _serialize_issues(course.issues),
    }


def _source_payload(source: SourceDocument) -> dict[str, Any]:
    return {
        "snapshot_id": source.snapshot_id,
        "kind": source.kind,
        "container": source.container,
        "original_entry_name": source.original_entry_name,
        "safe_filename": source.safe_filename,
        "sha256": source.sha256,
        "size_bytes": source.size_bytes,
    }


def _serialize_issues(issues: tuple[ImportIssue, ...]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for issue in issues:
        cell = None
        if issue.cell is not None:
            cell = {
                "sheet": issue.cell.sheet,
                "row": issue.cell.row,
                "column": issue.cell.column,
                "a1": issue.cell.a1,
            }
        values.append(
            {
                "code": issue.code,
                "message": issue.message,
                "severity": issue.severity.value,
                "cell": cell,
            }
        )
    return values


def _confidence_number(value: Confidence) -> float:
    return {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.75, Confidence.LOW: 0.4}[value]
