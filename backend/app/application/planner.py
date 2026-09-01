"""Application service that maps catalog rows to the pure scheduling engine."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..api.schemas import PLANNING_SCHEMA_VERSION, CourseChoice, PlanRequest
from ..domain import (
    AvailabilityStatus,
    BlockedTime,
    ConstraintStrength,
    Course,
    CourseRequest,
    InstructorRule,
    Meeting,
    SchedulingProblem,
    SectionOption,
    SelectionPhase,
    TeachingSection,
    TimePrecision,
    UserConstraints,
    WeekMask,
)
from ..infrastructure.tables import (
    CatalogCourse,
    CatalogSection,
    PlanningRun,
    User,
)
from ..scheduling import ScheduleSolver, SolverConfig
from .catalog_state import catalog_fingerprint


class PlanningInputError(ValueError):
    pass


def generate_schedule(
    db: Session,
    user: User,
    payload: PlanRequest,
    *,
    resolved_choices: list[CourseChoice] | None = None,
    additional_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Generate, verify and persist one planning run.

    ``resolved_choices`` is supplied by the curriculum service for curriculum
    mode.  In mixed mode the UI's edited manual list is authoritative: this is
    what makes removal of a curriculum course a real edit rather than having
    the server silently add it back.
    """

    choices = _deduplicate_choices(resolved_choices or payload.manual_courses)
    if not choices:
        raise PlanningInputError("没有可排课程；请先载入培养方案或手动添加课程")
    if (
        payload.preferences.phase == "retake"
        and not payload.preferences.retake_eligibility_confirmed
    ):
        raise PlanningInputError("重修选课仅适用于不及格或缓考课程；请先确认本人具备重修资格")

    course_ids = [choice.course_id for choice in choices]
    catalog_courses = list(
        db.scalars(
            select(CatalogCourse)
            .options(selectinload(CatalogCourse.sections))
            .where(CatalogCourse.id.in_(course_ids))
        ).unique()
    )
    by_id = {course.id: course for course in catalog_courses}
    unknown_ids = [course_id for course_id in course_ids if course_id not in by_id]
    if unknown_ids:
        raise PlanningInputError("以下课程不在已导入课程总库中：" + "、".join(unknown_ids))

    warnings: list[str] = list(additional_warnings or [])
    profile_preferences = (
        user.profile.preferences
        if user.profile and isinstance(user.profile.preferences, dict)
        else {}
    )
    administrative_class = str(
        profile_preferences.get("administrative_class") or ""
    ).strip()
    if user.profile and user.profile.cohort_year == 2024:
        warnings.append(
            "2026 年秋季选课规则特别提醒：2024 级本科生须在教务系统选择“社会调查”。"
            "当前课程总库未按该名称匹配到可排教学班，本软件不会把它静默视为已完成。"
        )
    domain_courses: list[Course] = []
    requests: list[CourseRequest] = []
    options: list[SectionOption] = []
    explicit_old_courses: set[str] = set()
    confirmed_unknown_options: set[str] = set()
    locked_options: set[str] = set()

    for choice in choices:
        catalog_course = by_id[choice.course_id]
        availability = _course_availability(catalog_course)
        domain_courses.append(
            Course(
                id=catalog_course.id,
                code=catalog_course.code,
                name=catalog_course.name,
                credits=catalog_course.credits or 0.0,
                availability=availability,
                availability_reason=_availability_reason(availability, catalog_course),
            )
        )
        requests.append(
            CourseRequest(
                course_id=catalog_course.id,
                priority=choice.priority,
                required=choice.required,
            )
        )
        if choice.allow_confirmation_required:
            explicit_old_courses.add(catalog_course.id)
        if choice.locked_section_id:
            locked_options.add(choice.locked_section_id)

        for row in catalog_course.sections:
            is_old_only = _is_old_only_section(row)
            has_unknown_time = _section_has_unknown_time(row)
            if (
                row.needs_confirmation
                and not is_old_only
                and not has_unknown_time
                and availability is AvailabilityStatus.AVAILABLE
                and not choice.allow_confirmation_required
            ):
                # Current but low-confidence parse records need the same
                # explicit data-risk confirmation as old-only evidence.
                continue
            if (
                is_old_only
                and availability is AvailabilityStatus.AVAILABLE
                and not choice.allow_confirmation_required
            ):
                # For a course that also exists in the newest snapshot, old
                # alternatives never leak into the candidate set implicitly.
                continue
            option, option_warnings = _section_to_option(row)
            options.append(option)
            warnings.extend(option_warnings)
            if choice.allow_unknown_time:
                confirmed_unknown_options.add(option.id)

    option_ids = {option.id for option in options}
    invalid_locked = locked_options - option_ids
    if invalid_locked:
        raise PlanningInputError("锁定的教学班不存在或不属于所选课程：" + "、".join(invalid_locked))

    constraints = _build_constraints(
        payload,
        explicitly_allowed_course_ids=explicit_old_courses,
        confirmed_unknown_time_option_ids=confirmed_unknown_options,
        locked_option_ids=locked_options,
        administrative_class=administrative_class or None,
    )
    problem = SchedulingProblem(
        courses=tuple(domain_courses),
        requests=tuple(requests),
        options=tuple(options),
        constraints=constraints,
    )
    solver = ScheduleSolver(
        SolverConfig(
            max_solutions=payload.preferences.max_solutions,
            time_limit_seconds=8.0,
            random_seed=0,
        )
    )
    result = solver.solve(problem)
    current_catalog_fingerprint = catalog_fingerprint(db)
    response = _serialize_result(result, problem, by_id, warnings, current_catalog_fingerprint)
    run_id = str(uuid4())
    response["run_id"] = run_id
    db.add(
        PlanningRun(
            id=run_id,
            user_id=user.id,
            input_mode=payload.input_mode.value,
            request_json=payload.model_dump(mode="json"),
            result_json=response,
            catalog_fingerprint=current_catalog_fingerprint,
        )
    )
    db.flush()
    return response


def _deduplicate_choices(choices: list[CourseChoice]) -> list[CourseChoice]:
    result: dict[str, CourseChoice] = {}
    for choice in choices:
        existing = result.get(choice.course_id)
        if existing is None:
            result[choice.course_id] = choice
            continue
        # Duplicate curriculum/manual entries merge conservatively: required
        # and explicit data-risk opt-ins are never silently discarded.
        result[choice.course_id] = CourseChoice(
            course_id=choice.course_id,
            priority=max(existing.priority, choice.priority),
            required=existing.required or choice.required,
            locked_section_id=choice.locked_section_id or existing.locked_section_id,
            allow_confirmation_required=(
                existing.allow_confirmation_required or choice.allow_confirmation_required
            ),
            allow_unknown_time=existing.allow_unknown_time or choice.allow_unknown_time,
        )
    return list(result.values())


def _course_availability(course: CatalogCourse) -> AvailabilityStatus:
    if any(
        not _is_old_only_section(section)
        and (not section.needs_confirmation or _section_has_unknown_time(section))
        for section in course.sections
    ):
        return AvailabilityStatus.AVAILABLE
    if any(section.needs_confirmation for section in course.sections):
        return AvailabilityStatus.NEEDS_CONFIRMATION
    return AvailabilityStatus.UNAVAILABLE


def _availability_reason(
    status: AvailabilityStatus,
    course: CatalogCourse,
) -> str | None:
    if status is AvailabilityStatus.NEEDS_CONFIRMATION:
        if all(_is_old_only_section(section) for section in course.sections):
            return "该课程仅见于旧版快照，最新版总库中未找到"
        return "该课程的教学班解析置信度不足，需要核对原始课表"
    if status is AvailabilityStatus.UNAVAILABLE:
        return "当前没有可用教学班"
    return None


def _is_old_only_section(section: CatalogSection) -> bool:
    return any(issue.get("code") == "old_snapshot_only" for issue in section.import_issues)


def _section_has_unknown_time(section: CatalogSection) -> bool:
    return any(
        meeting.get("precision") in {"week_only", "date_range", "tbd"}
        for meeting in section.meetings
    )


def _section_to_option(section: CatalogSection) -> tuple[SectionOption, list[str]]:
    warnings: list[str] = []
    meetings: list[Meeting] = []
    for index, raw in enumerate(section.meetings):
        meeting, warning = _parse_meeting(raw, section, index)
        meetings.append(meeting)
        if warning:
            warnings.append(warning)
    if not meetings:
        meetings.append(
            Meeting(
                weeks=WeekMask.empty(),
                precision=TimePrecision.TBD,
                source_ref=f"{section.id}:no-meeting",
            )
        )
        warnings.append(f"教学班 {section.display_name} 没有具体上课时间，默认不参与排课")

    teaching_section = TeachingSection(
        id=section.id,
        course_id=section.course_id,
        section_code=section.section_code,
        instructors=tuple(section.instructors),
        meetings=tuple(meetings),
        capacity=section.capacity,
        enrolled_count=section.enrolled_count,
        recommended_cohorts=tuple(section.composition),
    )
    return (
        SectionOption(
            id=section.id,
            course_id=section.course_id,
            sections=(teaching_section,),
            label=section.display_name,
            requires_confirmation=section.needs_confirmation,
        ),
        warnings,
    )


def _parse_meeting(
    raw: dict[str, Any], section: CatalogSection, index: int
) -> tuple[Meeting, str | None]:
    source_ref = str(raw.get("source_ref") or f"{section.id}:meeting:{index}")
    weeks_value = raw.get("weeks", [])
    try:
        weeks = WeekMask.from_weeks(int(value) for value in weeks_value)
    except (TypeError, ValueError):
        weeks = WeekMask.empty()
    try:
        precision = TimePrecision(str(raw.get("precision", "exact_slot")).casefold())
    except ValueError:
        precision = TimePrecision.TBD

    weekday = _optional_int(raw.get("weekday"))
    start = _optional_int(raw.get("start_period"))
    end = _optional_int(raw.get("end_period"))
    warning: str | None = None
    if precision is TimePrecision.EXACT_SLOT and (
        not weeks or weekday is None or start is None or end is None
    ):
        precision = TimePrecision.TBD
        weekday = start = end = None
        warning = f"教学班 {section.display_name} 的精确时段字段不完整，已降级为待确认并默认排除"
    if precision in {TimePrecision.WEEK_ONLY, TimePrecision.DATE_RANGE, TimePrecision.TBD}:
        weekday = start = end = None
    try:
        return (
            Meeting(
                weeks=weeks,
                weekday=weekday,
                start_period=start,
                end_period=end,
                campus=_optional_text(raw.get("campus")),
                room=_optional_text(raw.get("room")),
                precision=precision,
                source_ref=source_ref,
            ),
            warning,
        )
    except (TypeError, ValueError):
        return (
            Meeting(
                weeks=weeks,
                precision=TimePrecision.TBD,
                source_ref=source_ref,
            ),
            f"教学班 {section.display_name} 的时段值非法，已降级为待确认并默认排除",
        )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_constraints(
    payload: PlanRequest,
    *,
    explicitly_allowed_course_ids: set[str],
    confirmed_unknown_time_option_ids: set[str],
    locked_option_ids: set[str],
    administrative_class: str | None,
) -> UserConstraints:
    blocked_times = [
        BlockedTime(
            id=item.id,
            weekday=item.weekday,
            start_period=item.start_period,
            end_period=item.end_period,
            weeks=WeekMask.from_weeks(item.weeks),
            strength=ConstraintStrength(item.strength),
            penalty=item.penalty,
            label=item.label,
        )
        for item in payload.preferences.blocked_times
    ]
    # Convenience switches become transparent soft rules.  They are included
    # in explanations exactly like user-created blocked-time rules.
    if payload.preferences.prefer_no_early_class:
        for weekday in range(1, 8):
            blocked_times.append(
                BlockedTime(
                    id=f"preference-no-early-{weekday}",
                    weekday=weekday,
                    start_period=1,
                    end_period=2,
                    weeks=WeekMask.all(21),
                    strength=ConstraintStrength.SOFT,
                    penalty=25,
                    label="尽量不要早课",
                )
            )
    if payload.preferences.prefer_no_evening_class:
        for weekday in range(1, 8):
            blocked_times.append(
                BlockedTime(
                    id=f"preference-no-evening-{weekday}",
                    weekday=weekday,
                    start_period=9,
                    end_period=20,
                    weeks=WeekMask.all(21),
                    strength=ConstraintStrength.SOFT,
                    penalty=25,
                    label="尽量不要晚课",
                )
            )
    return UserConstraints(
        blocked_times=tuple(blocked_times),
        instructor_rules=tuple(
            InstructorRule(
                id=item.id,
                instructor=item.instructor,
                strength=ConstraintStrength(item.strength),
                penalty=item.penalty,
                label=item.label,
            )
            for item in payload.preferences.instructor_rules
        ),
        locked_option_ids=frozenset(locked_option_ids),
        forbidden_option_ids=frozenset(payload.preferences.forbidden_section_ids),
        explicitly_allowed_course_ids=frozenset(explicitly_allowed_course_ids),
        confirmed_unknown_time_option_ids=frozenset(confirmed_unknown_time_option_ids),
        prefer_compact_days=payload.preferences.prefer_compact_days,
        compact_day_penalty=10,
        recommended_cohort=administrative_class,
        non_recommended_cohort_penalty=200,
        phase=SelectionPhase(payload.preferences.phase),
    )


def _serialize_result(
    result: Any,
    problem: SchedulingProblem,
    catalog_courses: dict[str, CatalogCourse],
    warnings: list[str],
    catalog_fingerprint: str,
) -> dict[str, Any]:
    option_by_id = {option.id: option for option in problem.options}
    course_by_id = {course.id: course for course in problem.courses}
    catalog_section_by_id = {
        section.id for course in catalog_courses.values() for section in course.sections
    }
    catalog_section_rows = {
        section.id: section for course in catalog_courses.values() for section in course.sections
    }
    serialized_plans: list[dict[str, Any]] = []
    for plan in result.plans:
        selected_options = [option_by_id[option_id] for option_id in plan.selected_option_ids]
        meetings: list[dict[str, Any]] = []
        plan_warnings: list[str] = []
        selected_courses: list[dict[str, Any]] = []
        for option in selected_options:
            course = course_by_id[option.course_id]
            selected_catalog_rows = [
                catalog_section_rows[section_id]
                for section_id in option.section_ids
                if section_id in catalog_section_by_id
            ]
            compositions = list(
                dict.fromkeys(
                    value
                    for row in selected_catalog_rows
                    for value in row.composition
                    if value.strip()
                )
            )
            selected_courses.append(
                {
                    "course_id": course.id,
                    "course_code": course.code,
                    "course_name": course.name,
                    "option_id": option.id,
                    "section_ids": list(option.section_ids),
                    "section_codes": [row.section_code for row in selected_catalog_rows],
                    "section_names": [row.display_name for row in selected_catalog_rows],
                    "instructors": list(option.instructors),
                    "composition": compositions,
                }
            )
            recommended_cohort = problem.constraints.recommended_cohort
            if (
                recommended_cohort
                and compositions
                and not any(
                    _cohort_value_matches(recommended_cohort, value) for value in compositions
                )
            ):
                plan_warnings.append(
                    f"{course.code} {course.name} 选择的教学班面向 {', '.join(compositions)}，"
                    f"未列出你的行政班 {recommended_cohort}；这是跨班备选，须在教务系统核对资格"
                )
            if any(_is_old_only_section(row) for row in selected_catalog_rows):
                plan_warnings.append(
                    f"{course.code} {course.name} 仅来自旧版快照，必须在教务系统再次确认"
                )
            if any(
                row.needs_confirmation and not _is_old_only_section(row)
                for row in selected_catalog_rows
            ):
                plan_warnings.append(
                    f"{course.code} {course.name} 含低置信度或不完整时段记录，"
                    "必须结合原始课表再次确认"
                )
            for meeting in option.meetings:
                if not meeting.is_exact:
                    if meeting.precision is not TimePrecision.ASYNC:
                        plan_warnings.append(
                            f"{course.code} {course.name} 含“{meeting.precision.value}”时间，"
                            "未证明与其他课程无冲突"
                        )
                    continue
                section_code = option.sections[0].section_code
                meetings.append(
                    {
                        "course_id": course.id,
                        "course_code": course.code,
                        "course_name": course.name,
                        "option_id": option.id,
                        "section_code": section_code,
                        "weeks": list(meeting.weeks.weeks),
                        "weekday": meeting.weekday,
                        "start_period": meeting.start_period,
                        "end_period": meeting.end_period,
                        "campus": meeting.campus,
                        "room": meeting.room,
                    }
                )
        serialized_plans.append(
            {
                "selected_option_ids": list(plan.selected_option_ids),
                "selected_courses": selected_courses,
                "scheduled_course_count": len(selected_courses),
                "unscheduled_course_ids": list(plan.unscheduled_course_ids),
                "unscheduled_courses": [
                    {
                        "course_id": course_id,
                        "course_code": course_by_id[course_id].code,
                        "course_name": course_by_id[course_id].name,
                    }
                    for course_id in plan.unscheduled_course_ids
                ],
                "coverage_score": plan.coverage_score,
                "soft_penalty": plan.soft_penalty,
                "meetings": meetings,
                "warnings": list(dict.fromkeys(plan_warnings)),
                "explanations": [asdict(explanation) for explanation in plan.explanations],
            }
        )
    global_warnings = list(dict.fromkeys(warnings))
    if any(
        section.capacity is None
        for course in catalog_courses.values()
        for section in course.sections
    ):
        global_warnings.append(
            "课程总库没有可靠容量字段；已选人数不会被当作容量，最终余量请以教务系统为准"
        )
    global_warnings.append(_phase_warning(problem.constraints.phase))
    return {
        "schema_version": PLANNING_SCHEMA_VERSION,
        "run_id": "",
        "status": result.status.value,
        "plans": serialized_plans,
        "plan_limit": result.plan_limit,
        "all_plans_returned": result.all_plans_returned,
        "plans_truncated": result.plans_truncated,
        "diagnostics": [asdict(item) for item in result.diagnostics],
        "warnings": list(dict.fromkeys(global_warnings)),
        "catalog_fingerprint": catalog_fingerprint,
        "phase": problem.constraints.phase.value,
    }


def _cohort_value_matches(expected: str, registered: str) -> bool:
    expected_key = "".join(character for character in expected if character.isalnum()).casefold()
    registered_key = "".join(
        character for character in registered if character.isalnum()
    ).casefold()
    return bool(expected_key) and expected_key in registered_key


def _phase_warning(phase: SelectionPhase) -> str:
    if phase is SelectionPhase.PRESELECTION:
        return "当前按预选阶段解释：可超容量提交但并非先到先得，筛选后仍可能被随机移除。"
    if phase is SelectionPhase.RETAKE:
        return (
            "当前按重修阶段解释：仅不及格或缓考课程具备资格；"
            "软件仍不自动允许时间冲突，冲突免听须按学校流程另行申请。"
        )
    if phase is SelectionPhase.ADD_DROP:
        return "当前按补退选阶段解释：教学班通常先到先得；结束后一般不再开放统一调整。"
    return "当前按确认阶段解释：教学班通常先到先得，容量和开放状态必须在教务系统实时核对。"
