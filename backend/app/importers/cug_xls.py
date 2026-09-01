"""Importer for CUG (Wuhan) 2026 fall legacy BIFF ``.xls`` schedules."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    CatalogSnapshot,
    CellReference,
    Confidence,
    ImportedCourseSchedule,
    ImportedMeeting,
    ImportedTeachingClass,
    ImportIssue,
    IssueSeverity,
    SourceDocument,
    TimePrecision,
    lower_confidence,
)
from .safe_zip import (
    DEFAULT_ZIP_SAFETY_LIMITS,
    ZipSafetyLimits,
    read_safe_zip_entries,
    safe_windows_filename,
)

OLE2_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
DEFAULT_TOTAL_WEEKS = 21
DEFAULT_MAX_XLS_BYTES = 16 * 1024 * 1024

_FILENAME_RE = re.compile(r"^(?P<code>[^-]+)-(?P<name>.*)\((?P<token>\d+)\)\.xls$", re.IGNORECASE)
_HEADER_RE = re.compile(r"^\s*(?P<code>\S+)\s+(?P<college>.+?)\s*$")
_NOTE_RE = re.compile(
    r"本学期(?P<start>\d{4}-\d{2}-\d{2})正式上课至"
    r"(?P<end>\d{4}-\d{2}-\d{2})结束，共(?P<weeks>\d+)周"
    r".*?打印时间[：:]\s*(?P<printed>\d{4}-\d{2}-\d{2})"
)
_PERIOD_PREFIX_RE = re.compile(r"^\s*[（(](?P<start>\d+)-(?P<end>\d+)节[）)]\s*")
_WEEK_SEGMENT_RE = re.compile(
    r"^(?P<start>\d+)(?:-(?P<end>\d+))?周(?:[（(](?P<parity>单|双)[）)])?$"
)
_NORMAL_SECTION_RE = re.compile(r"-(?P<base>\d{4})(?P<suffix>[A-Za-z]?)(?![A-Za-z0-9])")
_LOOSE_SECTION_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<base>\d{4})(?P<suffix>[A-Za-z]?)(?![A-Za-z0-9])"
)
_PRACTICE_HEAD_RE = re.compile(r"^(?P<prefix>.*)[（(]共(?P<count>\d+)周[）)]$")
_INSTRUCTOR_SPLIT_RE = re.compile(r"[，,、;；]+")
_PRACTICE_SPLIT_RE = re.compile(r";[\s\u3000]{2,}")

_GRID_PERIODS: dict[int, tuple[int, int]] = {
    2: (1, 2),
    3: (3, 4),
    4: (5, 6),
    5: (7, 8),
    6: (9, 10),
    7: (11, 12),
}


class WorkbookImportError(ValueError):
    """Raised when bytes cannot be interpreted as a schedule workbook."""


@dataclass(frozen=True, slots=True)
class WeekExpression:
    raw: str
    weeks: tuple[int, ...]
    explicit_periods: tuple[int, int] | None
    confidence: Confidence
    issues: tuple[ImportIssue, ...]


@dataclass(frozen=True, slots=True)
class _FilenameMetadata:
    code: str | None
    name: str | None
    export_token: str | None


def parse_week_expression(
    raw: str,
    *,
    total_weeks: int = DEFAULT_TOTAL_WEEKS,
    cell: CellReference | None = None,
) -> WeekExpression:
    """Parse CUG week expressions, including comma segments and odd/even weeks."""

    value = raw.strip().replace("－", "-").replace("—", "-")
    explicit_periods: tuple[int, int] | None = None
    issues: list[ImportIssue] = []
    prefix = _PERIOD_PREFIX_RE.match(value)
    if prefix:
        start_period = int(prefix.group("start"))
        end_period = int(prefix.group("end"))
        if start_period < 1 or end_period < start_period:
            issues.append(
                _issue(
                    "period_expression_invalid",
                    f"Invalid period range in {raw!r}",
                    IssueSeverity.ERROR,
                    cell,
                )
            )
        else:
            explicit_periods = (start_period, end_period)
        value = value[prefix.end() :].strip()

    weeks: set[int] = set()
    invalid_segments: list[str] = []
    segments = [segment.strip() for segment in re.split(r"[,，]", value) if segment.strip()]
    if not segments:
        invalid_segments.append(value)
    for segment in segments:
        match = _WEEK_SEGMENT_RE.fullmatch(segment)
        if not match:
            invalid_segments.append(segment)
            continue
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        if start < 1 or end < start or end > total_weeks:
            invalid_segments.append(segment)
            continue
        parity = match.group("parity")
        for week in range(start, end + 1):
            if parity == "单" and week % 2 == 0:
                continue
            if parity == "双" and week % 2 == 1:
                continue
            weeks.add(week)
    if invalid_segments:
        issues.append(
            _issue(
                "week_expression_invalid",
                "Unparsed or out-of-term week segment(s): "
                + ", ".join(map(repr, invalid_segments)),
                IssueSeverity.ERROR,
                cell,
            )
        )
    confidence = Confidence.HIGH if not issues else Confidence.LOW
    return WeekExpression(
        raw=raw,
        weeks=tuple(sorted(weeks)),
        explicit_periods=explicit_periods,
        confidence=confidence,
        issues=tuple(issues),
    )


def import_schedule_zip(
    archive_path: str | Path,
    *,
    snapshot_id: str | None = None,
    strict: bool = False,
    limits: ZipSafetyLimits = DEFAULT_ZIP_SAFETY_LIMITS,
) -> CatalogSnapshot:
    """Import every safe ``.xls`` member directly from a ZIP archive."""

    archive = Path(archive_path)
    resolved_snapshot_id = snapshot_id or _default_snapshot_id(archive)
    entries, archive_issues = read_safe_zip_entries(
        archive,
        snapshot_id=resolved_snapshot_id,
        limits=limits,
    )
    courses: list[ImportedCourseSchedule] = []
    issues = list(archive_issues)
    for entry in entries:
        try:
            courses.append(parse_workbook_bytes(entry.data, source=entry.source))
        except (WorkbookImportError, ImportError) as exc:
            issue = _issue(
                "workbook_import_failed",
                f"{entry.source.original_entry_name!r}: {exc}",
                IssueSeverity.ERROR,
            )
            if strict:
                raise WorkbookImportError(issue.message) from exc
            issues.append(issue)
    issues.extend(_duplicate_course_issues(courses))
    return CatalogSnapshot(
        snapshot_id=resolved_snapshot_id,
        courses=tuple(courses),
        issues=tuple(issues),
    )


def import_schedule_files(
    paths: Iterable[str | Path],
    *,
    snapshot_id: str,
    strict: bool = False,
) -> CatalogSnapshot:
    """Import standalone legacy ``.xls`` files without trusting filename data."""

    courses: list[ImportedCourseSchedule] = []
    issues: list[ImportIssue] = []
    for supplied_path in paths:
        path = Path(supplied_path)
        try:
            courses.append(parse_workbook_file(path, snapshot_id=snapshot_id))
        except (OSError, WorkbookImportError, ImportError) as exc:
            issue = _issue(
                "workbook_import_failed",
                f"{path}: {exc}",
                IssueSeverity.ERROR,
            )
            if strict:
                raise WorkbookImportError(issue.message) from exc
            issues.append(issue)
    issues.extend(_duplicate_course_issues(courses))
    return CatalogSnapshot(snapshot_id=snapshot_id, courses=tuple(courses), issues=tuple(issues))


def parse_workbook_file(
    path: str | Path,
    *,
    snapshot_id: str,
    max_bytes: int = DEFAULT_MAX_XLS_BYTES,
) -> ImportedCourseSchedule:
    workbook_path = Path(path)
    size = workbook_path.stat().st_size
    if size > max_bytes:
        raise WorkbookImportError(f"workbook exceeds {max_bytes} bytes")
    data = workbook_path.read_bytes()
    source = SourceDocument(
        snapshot_id=snapshot_id,
        kind="file",
        container=str(workbook_path),
        original_entry_name=None,
        safe_filename=safe_windows_filename(workbook_path.name),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )
    return parse_workbook_bytes(data, source=source)


def parse_workbook_bytes(
    data: bytes,
    *,
    source: SourceDocument,
    max_bytes: int = DEFAULT_MAX_XLS_BYTES,
) -> ImportedCourseSchedule:
    """Read one OLE BIFF workbook from bytes through ``xlrd``."""

    if len(data) > max_bytes:
        raise WorkbookImportError(f"workbook exceeds {max_bytes} bytes")
    if not data.startswith(OLE2_SIGNATURE):
        raise WorkbookImportError("not an OLE2/BIFF .xls workbook")
    try:
        import xlrd  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency is declared by the project
        raise ImportError("xlrd>=2 is required to read legacy .xls files") from exc
    try:
        book = xlrd.open_workbook(file_contents=data, on_demand=True)
    except Exception as exc:
        raise WorkbookImportError(f"xlrd could not open workbook: {exc}") from exc
    try:
        if book.nsheets < 1:
            raise WorkbookImportError("workbook contains no worksheets")
        sheet = book.sheet_by_index(0)
        matrix = tuple(
            tuple(_cell_text(sheet.cell_value(row, column)) for column in range(sheet.ncols))
            for row in range(sheet.nrows)
        )
        return parse_schedule_matrix(matrix, source=source, sheet_name=sheet.name)
    finally:
        book.release_resources()


def parse_schedule_matrix(
    rows: Sequence[Sequence[Any]],
    *,
    source: SourceDocument,
    sheet_name: str = "Sheet0",
) -> ImportedCourseSchedule:
    """Parse already-read cell values; useful for deterministic unit tests."""

    matrix = tuple(tuple(_cell_text(value) for value in row) for row in rows)
    if len(matrix) < 8 or max((len(row) for row in matrix), default=0) < 9:
        raise WorkbookImportError("schedule sheet is smaller than the expected 8x9 grid")

    filename = source.original_entry_name or Path(source.container).name
    filename_meta = _parse_filename(filename)
    title_raw = _at(matrix, 0, 3)
    title_name = _strip_title_suffix(title_raw)
    header_raw = _at(matrix, 0, 7)
    header_match = _HEADER_RE.match(header_raw)
    header_code = header_match.group("code") if header_match else None
    college = header_match.group("college").strip() if header_match else None
    course_code = header_code or filename_meta.code
    if not course_code:
        raise WorkbookImportError("course code missing from both filename and workbook header")
    course_name = (title_name or filename_meta.name or "").strip()
    if not course_name:
        raise WorkbookImportError("course name missing from both filename and workbook title")

    issues: list[ImportIssue] = []
    if not header_match:
        issues.append(
            _issue(
                "header_metadata_unparsed",
                f"Could not parse course code and college from {header_raw!r}",
                IssueSeverity.WARNING,
                _cell(sheet_name, 0, 7),
            )
        )
    if (
        filename_meta.code
        and header_code
        and filename_meta.code.casefold() != header_code.casefold()
    ):
        issues.append(
            _issue(
                "course_code_mismatch",
                f"Filename code {filename_meta.code!r} differs from header code {header_code!r}",
                IssueSeverity.ERROR,
                _cell(sheet_name, 0, 7),
            )
        )
    if filename_meta.name and title_name and not _names_equivalent(filename_meta.name, title_name):
        issues.append(
            _issue(
                "course_name_mismatch",
                f"Filename name {filename_meta.name!r} differs from title {title_name!r}",
                IssueSeverity.WARNING,
                _cell(sheet_name, 0, 3),
            )
        )

    note_row, note_text = _find_prefixed_row(matrix, "注")
    note_match = _NOTE_RE.search(note_text) if note_text else None
    if note_match:
        term_start = note_match.group("start")
        term_end = note_match.group("end")
        total_weeks = int(note_match.group("weeks"))
        print_date = note_match.group("printed")
    else:
        term_start = term_end = print_date = None
        total_weeks = DEFAULT_TOTAL_WEEKS
        issues.append(
            _issue(
                "term_note_unparsed",
                "Term dates/print date were not recovered; using a 21-week validation bound",
                IssueSeverity.WARNING,
                _cell(sheet_name, note_row, 0) if note_row is not None else None,
            )
        )

    term = _at(matrix, 0, 0).strip() or None
    name_candidates = tuple(
        dict.fromkeys(
            name.strip()
            for name in (title_name, filename_meta.name)
            if name is not None and name.strip()
        )
    )
    meetings: list[ImportedMeeting] = []
    for row_index, grid_periods in _GRID_PERIODS.items():
        if row_index >= len(matrix):
            continue
        for column_index in range(2, 9):
            value = _at(matrix, row_index, column_index)
            if not value.strip():
                continue
            cell = _cell(sheet_name, row_index, column_index)
            for raw_line in value.splitlines():
                line = raw_line.strip()
                if line:
                    meetings.append(
                        _parse_ordinary_line(
                            line,
                            source=source,
                            cell=cell,
                            weekday=column_index - 1,
                            grid_periods=grid_periods,
                            total_weeks=total_weeks,
                            course_names=name_candidates,
                        )
                    )

    practice_row, practice_text = _find_prefixed_row(matrix, "实践课程：")
    if practice_text and practice_row is not None:
        meetings.extend(
            _parse_practice_cell(
                practice_text,
                source=source,
                cell=_cell(sheet_name, practice_row, 0),
                total_weeks=total_weeks,
                course_names=name_candidates,
            )
        )

    teaching_classes = _group_teaching_classes(course_code, meetings, source)
    return ImportedCourseSchedule(
        course_code=course_code,
        course_name=course_name,
        course_name_from_filename_raw=filename_meta.name,
        course_name_from_title_raw=title_name,
        offering_college=college,
        term=term,
        term_start=term_start,
        term_end=term_end,
        total_weeks=total_weeks,
        print_date=print_date,
        export_token=filename_meta.export_token,
        source=source,
        teaching_classes=teaching_classes,
        issues=tuple(issues),
    )


def _parse_ordinary_line(
    raw: str,
    *,
    source: SourceDocument,
    cell: CellReference,
    weekday: int,
    grid_periods: tuple[int, int],
    total_weeks: int,
    course_names: tuple[str, ...],
) -> ImportedMeeting:
    issues: list[ImportIssue] = []
    parts = [part.strip() for part in raw.split("/")]
    if len(parts) < 7:
        issues.append(
            _issue(
                "ordinary_fields_unrecoverable",
                f"Expected at least 7 slash-delimited fields, got {len(parts)}",
                IssueSeverity.ERROR,
                cell,
            )
        )
        parts += [""] * (7 - len(parts))
    instructors_raw = parts[0]
    week_raw = parts[1]
    assessment = parts[-1] or None
    enrolled_raw = parts[-2]
    composition_raw = parts[-3]
    middle = parts[2:-3]
    location_raw, class_label, split_issues = _split_location_and_class(
        middle, course_names=course_names, cell=cell
    )
    issues.extend(split_issues)

    weeks = parse_week_expression(week_raw, total_weeks=total_weeks, cell=cell)
    issues.extend(weeks.issues)
    periods = weeks.explicit_periods or grid_periods
    section_code, base_code, suffix, section_confidence, section_issues = _parse_section_code(
        class_label, cell=cell, allow_non_numeric=False
    )
    issues.extend(section_issues)
    campus, room, location_issues = _parse_location(location_raw, cell=cell)
    issues.extend(location_issues)
    enrolled_count = _parse_enrolled_count(enrolled_raw, issues=issues, cell=cell)
    instructors = _split_instructors(instructors_raw)
    if not instructors:
        issues.append(
            _issue(
                "instructor_missing",
                "Teaching record has no instructor text",
                IssueSeverity.WARNING,
                cell,
            )
        )
    precision = TimePrecision.EXACT_SLOT if weeks.weeks else TimePrecision.TBD
    confidence = lower_confidence(weeks.confidence, section_confidence)
    if any(issue.severity is IssueSeverity.ERROR for issue in issues):
        confidence = Confidence.LOW
    elif issues and confidence is Confidence.HIGH:
        confidence = Confidence.MEDIUM
    return ImportedMeeting(
        raw=raw,
        source=source,
        cell=cell,
        instructors_raw=instructors_raw,
        instructors=instructors,
        week_expression_raw=week_raw,
        weeks=weeks.weeks,
        weekday=weekday,
        start_period=periods[0],
        end_period=periods[1],
        campus=campus,
        room=room,
        location_raw=location_raw or None,
        class_label_raw=class_label,
        section_code=section_code,
        section_base_code=base_code,
        section_suffix=suffix,
        class_composition_raw=composition_raw,
        class_composition=_split_composition(composition_raw),
        enrolled_count_snapshot=enrolled_count,
        assessment=assessment,
        precision=precision,
        confidence=confidence,
        issues=tuple(issues),
    )


def _split_location_and_class(
    middle: Sequence[str],
    *,
    course_names: tuple[str, ...],
    cell: CellReference,
) -> tuple[str, str, tuple[ImportIssue, ...]]:
    if len(middle) < 2:
        issue = _issue(
            "ordinary_fields_unrecoverable",
            "Location and teaching-class fields could not be separated",
            IssueSeverity.ERROR,
            cell,
        )
        return "/".join(middle), "", (issue,)
    scored: list[tuple[int, int, str, str]] = []
    normalized_names = [_normalize_name(name) for name in course_names]
    for index in range(1, len(middle)):
        location = "/".join(middle[:index]).strip()
        class_label = "/".join(middle[index:]).strip()
        normalized_label = _normalize_name(class_label)
        score = 0
        if _LOOSE_SECTION_RE.search(class_label):
            score += 5
        if "校区" in location:
            score += 3
        if "校区" in class_label:
            score -= 5
        for name in normalized_names:
            if not name:
                continue
            if normalized_label.startswith(name):
                score += 7
            elif name in normalized_label:
                score += 4
            if name in _normalize_name(location):
                score -= 3
        scored.append((score, index, location, class_label))
    best_score = max(item[0] for item in scored)
    best = [item for item in scored if item[0] == best_score]
    # A later boundary is safer for room names containing '/', unless a full
    # course-name prefix made an earlier split strictly better.
    selected = max(best, key=lambda item: item[1])
    issues: list[ImportIssue] = []
    if len(best) > 1:
        issues.append(
            _issue(
                "slash_boundary_inferred",
                "Slash inside location or course label required an inferred field boundary",
                IssueSeverity.WARNING,
                cell,
            )
        )
    return selected[2], selected[3], tuple(issues)


def _parse_location(
    location_raw: str, *, cell: CellReference
) -> tuple[str | None, str | None, tuple[ImportIssue, ...]]:
    match = re.match(r"^\s*(?P<campus>\S*?校区)\s*(?P<room>.*)\s*$", location_raw)
    if not match:
        return (
            None,
            location_raw.strip() or None,
            (
                _issue(
                    "campus_unparsed",
                    f"Campus was not recovered from {location_raw!r}",
                    IssueSeverity.WARNING,
                    cell,
                ),
            ),
        )
    return match.group("campus"), match.group("room").strip() or None, ()


def _parse_section_code(
    class_label: str,
    *,
    cell: CellReference,
    allow_non_numeric: bool,
) -> tuple[str | None, str | None, str | None, Confidence, tuple[ImportIssue, ...]]:
    matches = tuple(_NORMAL_SECTION_RE.finditer(class_label))
    match = matches[-1] if matches else None
    if match:
        base = match.group("base")
        suffix = match.group("suffix") or None
        return base + (suffix or ""), base, suffix, Confidence.HIGH, ()
    loose_matches = tuple(_LOOSE_SECTION_RE.finditer(class_label))
    loose = loose_matches[-1] if loose_matches else None
    if loose:
        base = loose.group("base")
        suffix = loose.group("suffix") or None
        issue = _issue(
            "section_hyphen_missing",
            f"Recovered section {base + (suffix or '')!r} without the usual hyphen",
            IssueSeverity.WARNING,
            cell,
        )
        return base + (suffix or ""), base, suffix, Confidence.LOW, (issue,)
    if allow_non_numeric and class_label.strip():
        issue = _issue(
            "non_numeric_practice_identity",
            "Practice class has no numeric section code; full label and cohort form its identity",
            IssueSeverity.WARNING,
            cell,
        )
        return None, None, None, Confidence.MEDIUM, (issue,)
    issue = _issue(
        "class_identity_missing",
        f"No teaching-class code could be recovered from {class_label!r}",
        IssueSeverity.ERROR,
        cell,
    )
    return None, None, None, Confidence.LOW, (issue,)


def _parse_practice_cell(
    value: str,
    *,
    source: SourceDocument,
    cell: CellReference,
    total_weeks: int,
    course_names: tuple[str, ...],
) -> tuple[ImportedMeeting, ...]:
    content = value.split("：", 1)[1] if "：" in value else value.split(":", 1)[-1]
    records = [record.strip(" ;\r\n\t") for record in _PRACTICE_SPLIT_RE.split(content)]
    return tuple(
        _parse_practice_record(
            record,
            source=source,
            cell=cell,
            total_weeks=total_weeks,
            course_names=course_names,
        )
        for record in records
        if record
    )


def _parse_practice_record(
    raw: str,
    *,
    source: SourceDocument,
    cell: CellReference,
    total_weeks: int,
    course_names: tuple[str, ...],
) -> ImportedMeeting:
    issues: list[ImportIssue] = []
    parts = [part.strip() for part in raw.split("/")]
    if len(parts) < 4:
        issues.append(
            _issue(
                "practice_fields_unrecoverable",
                f"Expected at least 4 slash-delimited fields, got {len(parts)}",
                IssueSeverity.ERROR,
                cell,
            )
        )
        parts += [""] * (4 - len(parts))
    head = "/".join(parts[:-3]).strip()
    week_raw, class_label, composition_raw = parts[-3:]
    declared_weeks: int | None = None
    instructor_raw = ""
    head_match = _PRACTICE_HEAD_RE.fullmatch(head)
    if head_match:
        prefix = head_match.group("prefix")
        declared_weeks = int(head_match.group("count"))
        matching_names = [name for name in course_names if prefix.startswith(name)]
        if matching_names:
            matched_name = max(matching_names, key=len)
            instructor_raw = prefix[len(matched_name) :].strip()
        else:
            issues.append(
                _issue(
                    "practice_instructor_boundary_unparsed",
                    f"Could not separate course and instructor in {head!r}",
                    IssueSeverity.WARNING,
                    cell,
                )
            )
    else:
        issues.append(
            _issue(
                "practice_head_unparsed",
                f"Could not parse declared duration from {head!r}",
                IssueSeverity.WARNING,
                cell,
            )
        )
    weeks = parse_week_expression(week_raw, total_weeks=total_weeks, cell=cell)
    issues.extend(weeks.issues)
    if declared_weeks is not None and declared_weeks != len(weeks.weeks):
        issues.append(
            _issue(
                "practice_duration_mismatch",
                f"Declared {declared_weeks} weeks but expression expands to {len(weeks.weeks)}",
                IssueSeverity.WARNING,
                cell,
            )
        )
    section_code, base_code, suffix, section_confidence, section_issues = _parse_section_code(
        class_label, cell=cell, allow_non_numeric=True
    )
    issues.extend(section_issues)
    issues.append(
        _issue(
            "practice_time_unknown",
            "Concentrated-practice record has weeks but no weekday, periods, campus or room",
            IssueSeverity.WARNING,
            cell,
        )
    )
    confidence = lower_confidence(weeks.confidence, section_confidence, Confidence.MEDIUM)
    if any(issue.severity is IssueSeverity.ERROR for issue in issues):
        confidence = Confidence.LOW
    return ImportedMeeting(
        raw=raw,
        source=source,
        cell=cell,
        instructors_raw=instructor_raw,
        instructors=_split_instructors(instructor_raw),
        week_expression_raw=week_raw,
        weeks=weeks.weeks,
        weekday=None,
        start_period=None,
        end_period=None,
        campus=None,
        room=None,
        location_raw=None,
        class_label_raw=class_label,
        section_code=section_code,
        section_base_code=base_code,
        section_suffix=suffix,
        class_composition_raw=composition_raw,
        class_composition=_split_composition(composition_raw),
        enrolled_count_snapshot=None,
        assessment=None,
        precision=TimePrecision.WEEK_ONLY if weeks.weeks else TimePrecision.TBD,
        confidence=confidence,
        issues=tuple(issues),
    )


def _group_teaching_classes(
    course_code: str,
    meetings: Sequence[ImportedMeeting],
    source: SourceDocument,
) -> tuple[ImportedTeachingClass, ...]:
    grouped: OrderedDict[tuple[str, str], list[ImportedMeeting]] = OrderedDict()
    for meeting in meetings:
        grouped.setdefault(meeting.identity_key, []).append(meeting)
    result: list[ImportedTeachingClass] = []
    for records in grouped.values():
        first = records[0]
        issues = [issue for record in records for issue in record.issues]
        counts = {
            record.enrolled_count_snapshot
            for record in records
            if record.enrolled_count_snapshot is not None
        }
        assessments = {record.assessment for record in records if record.assessment is not None}
        if len(counts) <= 1:
            count = next(iter(counts), None)
        else:
            count = None
            issues.append(
                _issue(
                    "enrolled_count_inconsistent",
                    "Selected-student snapshot differs across meetings; "
                    "section aggregate left unknown",
                    IssueSeverity.WARNING,
                    first.cell,
                )
            )
        if len(assessments) <= 1:
            assessment = next(iter(assessments), None)
        else:
            assessment = None
            issues.append(
                _issue(
                    "assessment_inconsistent",
                    "Assessment differs across meetings; section aggregate left unknown",
                    IssueSeverity.WARNING,
                    first.cell,
                )
            )
        confidence = lower_confidence(*(record.confidence for record in records))
        reliable = bool(records) and all(record.reliable_for_scheduling for record in records)
        needs_confirmation = not reliable or any(
            record.precision is not TimePrecision.EXACT_SLOT for record in records
        )
        result.append(
            ImportedTeachingClass(
                course_code=course_code,
                class_label_raw=first.class_label_raw,
                section_code=first.section_code,
                section_base_code=first.section_base_code,
                section_suffix=first.section_suffix,
                class_composition_raw=first.class_composition_raw,
                class_composition=first.class_composition,
                instructors=tuple(
                    dict.fromkeys(
                        instructor for record in records for instructor in record.instructors
                    )
                ),
                meetings=tuple(records),
                enrolled_count_snapshot=count,
                assessment=assessment,
                source=source,
                confidence=confidence,
                issues=tuple(issues),
                needs_confirmation=needs_confirmation,
                reliable_for_scheduling=reliable,
            )
        )
    return tuple(result)


def _parse_enrolled_count(
    raw: str, *, issues: list[ImportIssue], cell: CellReference
) -> int | None:
    value = raw.strip()
    if re.fullmatch(r"\d+", value):
        return int(value)
    issues.append(
        _issue(
            "enrolled_count_unparsed",
            f"Selected-student count is not a non-negative integer: {raw!r}",
            IssueSeverity.WARNING,
            cell,
        )
    )
    return None


def _split_instructors(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _INSTRUCTOR_SPLIT_RE.split(value) if part.strip())


def _split_composition(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.split(r"[;；]", value) if part.strip())


def _parse_filename(filename: str) -> _FilenameMetadata:
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    match = _FILENAME_RE.fullmatch(basename)
    if not match:
        return _FilenameMetadata(None, None, None)
    return _FilenameMetadata(
        code=match.group("code").strip(),
        name=match.group("name"),
        export_token=match.group("token"),
    )


def _strip_title_suffix(title: str) -> str | None:
    value = title.strip()
    if not value:
        return None
    return value[: -len("课程课表")] if value.endswith("课程课表") else value


def _normalize_name(value: str) -> str:
    normalized = value.strip().casefold()
    normalized = re.sub(r"[/_:：]", "-", normalized)
    return re.sub(r"[\s\-]+", "", normalized)


def _names_equivalent(left: str, right: str) -> bool:
    return _normalize_name(left) == _normalize_name(right)


def _find_prefixed_row(
    matrix: Sequence[Sequence[str]], prefix: str
) -> tuple[int | None, str | None]:
    for row_index, row in enumerate(matrix):
        if row and row[0].strip().startswith(prefix):
            return row_index, row[0]
    return None, None


def _duplicate_course_issues(
    courses: Sequence[ImportedCourseSchedule],
) -> tuple[ImportIssue, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for course in courses:
        key = course.course_code.casefold()
        if key in seen:
            duplicates.add(course.course_code)
        seen.add(key)
    return tuple(
        _issue(
            "duplicate_course_workbook",
            f"Snapshot contains more than one workbook for course {code!r}",
            IssueSeverity.WARNING,
        )
        for code in sorted(duplicates)
    )


def _default_snapshot_id(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()[:12]
    return f"{path.stem}-{digest}"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _at(matrix: Sequence[Sequence[str]], row: int, column: int) -> str:
    if row < 0 or row >= len(matrix) or column < 0 or column >= len(matrix[row]):
        return ""
    return matrix[row][column]


def _cell(sheet: str, zero_based_row: int, zero_based_column: int) -> CellReference:
    return CellReference(sheet=sheet, row=zero_based_row + 1, column=zero_based_column + 1)


def _issue(
    code: str,
    message: str,
    severity: IssueSeverity,
    cell: CellReference | None = None,
) -> ImportIssue:
    return ImportIssue(code=code, message=message, severity=severity, cell=cell)
