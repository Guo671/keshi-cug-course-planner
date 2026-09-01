"""Conservative multi-snapshot merge policy for imported course schedules."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum

from .models import (
    CatalogSnapshot,
    ImportedCourseSchedule,
    ImportedTeachingClass,
    ImportIssue,
    IssueSeverity,
)


class MergeDisposition(StrEnum):
    NEW_ONLY = "new_only"
    NEW_PREFERRED = "new_preferred"
    OLD_ONLY = "old_only"


@dataclass(frozen=True, slots=True)
class MergedTeachingClass:
    """A selected version plus every source version retained for audit."""

    selected: ImportedTeachingClass
    versions: tuple[ImportedTeachingClass, ...]
    disposition: MergeDisposition
    needs_confirmation: bool
    reliable_for_scheduling: bool
    eligible_for_scheduling: bool
    issues: tuple[ImportIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class MergedCourse:
    course_code: str
    selected_metadata: ImportedCourseSchedule
    metadata_versions: tuple[ImportedCourseSchedule, ...]
    teaching_classes: tuple[MergedTeachingClass, ...]


@dataclass(frozen=True, slots=True)
class MergeStatistics:
    new_only: int
    new_preferred: int
    old_only: int
    structurally_changed: int
    enrollment_only_changed: int
    eligible_for_scheduling: int


@dataclass(frozen=True, slots=True)
class MergedCatalog:
    newest_snapshot_id: str
    legacy_snapshot_ids: tuple[str, ...]
    include_old_only: bool
    courses: tuple[MergedCourse, ...]
    issues: tuple[ImportIssue, ...]
    statistics: MergeStatistics

    @property
    def teaching_classes(self) -> tuple[MergedTeachingClass, ...]:
        return tuple(section for course in self.courses for section in course.teaching_classes)

    @property
    def scheduling_candidates(self) -> tuple[ImportedTeachingClass, ...]:
        return tuple(
            section.selected for section in self.teaching_classes if section.eligible_for_scheduling
        )


def merge_snapshots(
    newest: CatalogSnapshot,
    *legacy: CatalogSnapshot,
    include_old_only: bool = False,
) -> MergedCatalog:
    """Apply strategy A: newest wins; legacy-only records require explicit opt-in.

    ``legacy`` is ordered newest-to-oldest.  Exact source versions are always
    retained.  Opt-in changes only candidate eligibility; an old-only record
    remains marked unreliable and in need of confirmation.
    """

    new_sections = _index_sections(newest)
    old_sections = _index_legacy_sections(legacy)
    course_versions = _index_courses((newest, *legacy))
    all_course_keys = tuple(
        OrderedDict.fromkeys(
            [course.course_code.casefold() for course in newest.courses]
            + [course.course_code.casefold() for snapshot in legacy for course in snapshot.courses]
        )
    )
    merged_courses: list[MergedCourse] = []
    catalog_issues = [*newest.issues, *(issue for item in legacy for issue in item.issues)]
    new_only = new_preferred = old_only = structural_changes = enrollment_updates = eligible = 0

    for course_key in all_course_keys:
        metadata_versions = tuple(course_versions[course_key])
        selected_metadata = next(
            (course for course in newest.courses if course.course_code.casefold() == course_key),
            metadata_versions[0],
        )
        section_keys = tuple(
            OrderedDict.fromkeys(
                [key for key in new_sections if key[0] == course_key]
                + [key for key in old_sections if key[0] == course_key]
            )
        )
        merged_sections: list[MergedTeachingClass] = []
        for key in section_keys:
            current_versions = new_sections.get(key, ())
            legacy_versions = old_sections.get(key, ())
            section_issues: list[ImportIssue] = []
            if current_versions:
                selected = current_versions[0]
                versions = _unique_versions((*current_versions, *legacy_versions))
                if legacy_versions:
                    disposition = MergeDisposition.NEW_PREFERRED
                    new_preferred += 1
                    structural_changed = any(
                        _structural_signature(selected) != _structural_signature(previous)
                        for previous in legacy_versions
                    )
                    enrollment_changed = any(
                        selected.enrolled_count_snapshot != previous.enrolled_count_snapshot
                        for previous in legacy_versions
                    )
                    if structural_changed:
                        structural_changes += 1
                        section_issues.append(
                            ImportIssue(
                                code="snapshot_structure_changed",
                                message=(
                                    "Teaching-class structure changed; newest snapshot selected "
                                    "and "
                                    "all older versions retained"
                                ),
                                severity=IssueSeverity.WARNING,
                            )
                        )
                    elif enrollment_changed:
                        enrollment_updates += 1
                        section_issues.append(
                            ImportIssue(
                                code="enrollment_snapshot_updated",
                                message=(
                                    "Only the selected-student snapshot changed; it is not capacity"
                                ),
                                severity=IssueSeverity.INFO,
                            )
                        )
                else:
                    disposition = MergeDisposition.NEW_ONLY
                    new_only += 1
                needs_confirmation = selected.needs_confirmation
                reliable = selected.reliable_for_scheduling
                is_eligible = reliable
            else:
                selected = legacy_versions[0]
                versions = _unique_versions(legacy_versions)
                disposition = MergeDisposition.OLD_ONLY
                old_only += 1
                needs_confirmation = True
                reliable = False
                # Explicit opt-in admits only an otherwise exact/reliable old
                # record.  It never makes week-only practice data schedulable.
                is_eligible = include_old_only and selected.reliable_for_scheduling
                section_issues.append(
                    ImportIssue(
                        code="old_snapshot_only",
                        message=(
                            "Absent from newest snapshot; absence is not proof of cancellation. "
                            "Confirmation is required"
                        ),
                        severity=IssueSeverity.WARNING,
                    )
                )
            if is_eligible:
                eligible += 1
            merged_sections.append(
                MergedTeachingClass(
                    selected=selected,
                    versions=versions,
                    disposition=disposition,
                    needs_confirmation=needs_confirmation,
                    reliable_for_scheduling=reliable,
                    eligible_for_scheduling=is_eligible,
                    issues=tuple(section_issues),
                )
            )
        merged_courses.append(
            MergedCourse(
                course_code=selected_metadata.course_code,
                selected_metadata=selected_metadata,
                metadata_versions=metadata_versions,
                teaching_classes=tuple(merged_sections),
            )
        )
    statistics = MergeStatistics(
        new_only=new_only,
        new_preferred=new_preferred,
        old_only=old_only,
        structurally_changed=structural_changes,
        enrollment_only_changed=enrollment_updates,
        eligible_for_scheduling=eligible,
    )
    return MergedCatalog(
        newest_snapshot_id=newest.snapshot_id,
        legacy_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in legacy),
        include_old_only=include_old_only,
        courses=tuple(merged_courses),
        issues=tuple(catalog_issues),
        statistics=statistics,
    )


def _index_sections(
    snapshot: CatalogSnapshot,
) -> OrderedDict[tuple[str, str, str], tuple[ImportedTeachingClass, ...]]:
    accumulated: OrderedDict[tuple[str, str, str], list[ImportedTeachingClass]] = OrderedDict()
    for section in snapshot.teaching_classes:
        accumulated.setdefault(section.identity_key, []).append(section)
    return OrderedDict((key, tuple(value)) for key, value in accumulated.items())


def _index_legacy_sections(
    snapshots: tuple[CatalogSnapshot, ...],
) -> OrderedDict[tuple[str, str, str], tuple[ImportedTeachingClass, ...]]:
    accumulated: OrderedDict[tuple[str, str, str], list[ImportedTeachingClass]] = OrderedDict()
    for snapshot in snapshots:
        for section in snapshot.teaching_classes:
            accumulated.setdefault(section.identity_key, []).append(section)
    return OrderedDict((key, tuple(value)) for key, value in accumulated.items())


def _index_courses(
    snapshots: tuple[CatalogSnapshot, ...],
) -> OrderedDict[str, list[ImportedCourseSchedule]]:
    indexed: OrderedDict[str, list[ImportedCourseSchedule]] = OrderedDict()
    for snapshot in snapshots:
        for course in snapshot.courses:
            indexed.setdefault(course.course_code.casefold(), []).append(course)
    return indexed


def _unique_versions(
    versions: tuple[ImportedTeachingClass, ...],
) -> tuple[ImportedTeachingClass, ...]:
    seen: set[tuple[str, str]] = set()
    unique: list[ImportedTeachingClass] = []
    for version in versions:
        marker = (version.source.snapshot_id, version.source.sha256)
        if marker not in seen:
            seen.add(marker)
            unique.append(version)
    return tuple(unique)


def _structural_signature(section: ImportedTeachingClass) -> tuple[object, ...]:
    meetings = tuple(
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
    )
    return (
        section.class_label_raw.strip(),
        section.section_code,
        tuple(sorted(section.class_composition, key=str.casefold)),
        tuple(sorted(section.instructors, key=str.casefold)),
        meetings,
        section.assessment,
    )
