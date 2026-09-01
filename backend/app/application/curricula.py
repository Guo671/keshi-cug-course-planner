"""Official curriculum registry lookup and catalog matching."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from rapidfuzz.fuzz import ratio
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..api.schemas import (
    CourseChoice,
    CurriculumCourseResponse,
    CurriculumPreviewResponse,
    CurriculumSelection,
    CurriculumSourceResponse,
)
from ..config import settings
from ..infrastructure.tables import CatalogCourse, StudentProfile


class CurriculumError(ValueError):
    pass


_COLLEGE_ALIASES = {
    "自动化学院": "人工智能与自动化学院",
    "地球科学学院": "地球与行星科学学院",
    "公共管理学院": "公共管理与法学院",
}


@dataclass(frozen=True, slots=True)
class RegistryMatch:
    source: dict[str, Any] | None
    exact_identity: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CurriculumResolution:
    """Safe, schedulable subset plus every omission the student must review."""

    choices: tuple[CourseChoice, ...]
    warnings: tuple[str, ...]


class CurriculumRegistry:
    def __init__(self, registry_path: Path | None = None) -> None:
        self.registry_path = registry_path or settings.curriculum_registry_path
        self.base_dir = self.registry_path.parent

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CurriculumError("培养方案来源登记文件不存在") from exc
        except json.JSONDecodeError as exc:
            raise CurriculumError("培养方案来源登记文件格式错误") from exc
        if not isinstance(payload, dict):
            raise CurriculumError("培养方案来源登记顶层必须是对象")
        return cast(dict[str, Any], payload)

    def sources(self) -> list[dict[str, Any]]:
        value = self.load().get("sources", [])
        if not isinstance(value, list):
            raise CurriculumError("培养方案来源登记缺少 sources 列表")
        return [item for item in value if isinstance(item, dict)]

    def find_for_profile(self, profile: StudentProfile) -> RegistryMatch:
        profile_major = _normalize(profile.major)
        profile_code = _normalize(profile.major_code or "")
        profile_college = _canonical_college(profile.college)
        profile_variant = _normalize(profile.plan_variant or "")
        profile_cooperative = _is_cooperative_profile(profile.cooperation_program)

        same_name = [
            source
            for source in self.sources()
            if _normalize(source.get("major", "")) == profile_major
        ]
        same_code = [
            source
            for source in self.sources()
            if profile_code and _normalize(source.get("major_code", "")) == profile_code
        ]
        if profile_code:
            same_major = [source for source in same_name if source in same_code]
            if not same_major and (same_name or same_code):
                registered_names = "、".join(
                    sorted({str(source.get("major")) for source in same_code})
                )
                registered_codes = "、".join(
                    sorted({str(source.get("major_code")) for source in same_name})
                )
                details = []
                if registered_codes:
                    details.append(f"“{profile.major}”登记的专业代码为 {registered_codes}")
                if registered_names:
                    details.append(f"代码 {profile.major_code} 登记的专业为“{registered_names}”")
                return RegistryMatch(
                    source=None,
                    exact_identity=False,
                    warnings=(
                        "专业名称与专业代码相互矛盾（" + "；".join(details) +
                        "）。为防止串用其他专业培养方案，当前只能手动输入；请修正学生信息后重试。",
                    ),
                )
        else:
            same_major = same_name
        if not same_major:
            return RegistryMatch(
                source=None,
                exact_identity=False,
                warnings=("官网来源登记中没有找到该专业的当前完整培养方案，只能手动输入。",),
            )

        same_college = [
            source
            for source in same_major
            if _canonical_college(str(source.get("college_name", ""))) == profile_college
        ]
        if not same_college:
            colleges = "、".join(sorted({str(item.get("college_name")) for item in same_major}))
            return RegistryMatch(
                source=None,
                exact_identity=False,
                warnings=(
                    f"找到了同名专业，但官网登记归属为“{colleges}”，与填写学院不一致；"
                    "为防止套用错误方案，当前只能手动输入。",
                ),
            )

        same_variant = [
            source
            for source in same_college
            if _normalize(source.get("variant") or "") == profile_variant
            and bool(source.get("cooperative")) is profile_cooperative
        ]
        if not same_variant:
            variants = sorted(
                {str(item.get("variant") or "普通班") for item in same_college}
            )
            return RegistryMatch(
                source=None,
                exact_identity=False,
                warnings=(
                    f"该专业只登记了以下班型：{'、'.join(variants)}；"
                    "与你填写的培养方案变体不一致，只能手动输入。",
                ),
            )

        applicable = [
            source for source in same_variant if _cohort_applies(source, profile.cohort_year)
        ]
        if not applicable:
            return RegistryMatch(
                source=same_variant[0],
                exact_identity=False,
                warnings=(
                    f"已找到该专业官网来源，但未证明适用于 {profile.cohort_year} 级；"
                    "只能手动输入。",
                ),
            )
        # Prefer stronger access level, then the most recent publication.
        applicable.sort(
            key=lambda source: (
                {"A": 0, "B": 1, "C": 2, "D": 3}.get(
                    str(source.get("access_level")), 9
                ),
                str(source.get("published_at") or ""),
            )
        )
        return RegistryMatch(source=applicable[0], exact_identity=True, warnings=())

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        return next((item for item in self.sources() if item.get("id") == source_id), None)

    def load_plan_for_source(self, source: dict[str, Any]) -> dict[str, Any] | None:
        if source.get("access_level") != "A":
            return None
        major_code = str(source.get("major_code") or "")
        for path in sorted((self.base_dir / "plans").glob("*.json")):
            try:
                plan_payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(plan_payload, dict):
                continue
            plan = cast(dict[str, Any], plan_payload)
            if str(plan.get("major_code") or "") == major_code:
                return plan
        return None


def preview_for_profile(
    db: Session,
    profile: StudentProfile,
    *,
    semester: int,
    registry: CurriculumRegistry | None = None,
) -> CurriculumPreviewResponse:
    registry = registry or CurriculumRegistry()
    match = registry.find_for_profile(profile)
    source_payload = _source_response(match.source) if match.source else None
    warnings = list(match.warnings)
    if not match.exact_identity or match.source is None:
        return CurriculumPreviewResponse(
            source=source_payload,
            semester=semester,
            courses=[],
            manual_only=True,
            warnings=warnings,
        )

    source = match.source
    access_level = str(source.get("access_level"))
    if access_level != "A":
        warnings.append(_manual_only_message(access_level, source))
        return CurriculumPreviewResponse(
            source=source_payload,
            semester=semester,
            courses=[],
            manual_only=True,
            warnings=warnings,
        )

    plan = registry.load_plan_for_source(source)
    if plan is None:
        warnings.append("来源登记为可解析，但本地没有通过校验的结构化方案；已安全降级为手动输入。")
        return CurriculumPreviewResponse(
            source=source_payload,
            semester=semester,
            courses=[],
            manual_only=True,
            warnings=warnings,
        )
    courses, matching_warnings = _courses_for_semester(db, plan, semester)
    warnings.extend(matching_warnings)
    warnings.append(
        "年级到学期的映射必须由学生确认；转专业、休学、留级或提前修读时请改用实际学期。"
    )
    return CurriculumPreviewResponse(
        source=source_payload,
        semester=semester,
        courses=courses,
        manual_only=False,
        warnings=list(dict.fromkeys(warnings)),
    )


def resolve_required_curriculum_choices(
    db: Session,
    profile: StudentProfile,
    selection: CurriculumSelection,
) -> CurriculumResolution:
    preview = validate_curriculum_selection(db, profile, selection)
    required = [course for course in preview.courses if course.required]
    choice_groups = sorted(
        {
            course.selection_group or "未命名方向组"
            for course in preview.courses
            if course.requirement_type == "track_choice"
        }
    )
    warnings: list[str] = []
    if choice_groups:
        warnings.append(
            "培养方案中的以下方向课程组没有被自动选择："
            f"{'、'.join(choice_groups)}。请在混合方式中自行选择后重新排课。"
        )
    risk_confirmation = [
        course
        for course in required
        if course.matched_course_id
        and course.eligible_section_count == 0
        and (
            course.confirmation_required_section_count > 0
            or course.unknown_time_section_count > 0
        )
    ]
    if risk_confirmation:
        labels = "、".join(f"{course.code} {course.name}" for course in risk_confirmation)
        warnings.append(
            "以下必修课程只有需人工确认的数据，本次纯方案未自动排入："
            f"{labels}。请使用混合方式逐门确认风险后重新排课。"
        )
    unmatched = [course for course in required if not course.matched_course_id]
    if unmatched:
        labels = "、".join(f"{course.code} {course.name}" for course in unmatched)
        warnings.append(
            "培养方案中的以下必修课程在当前课程总库中未匹配，本次纯方案未自动排入且不会静默视为完成："
            f"{labels}。请使用混合方式核对、搜索并补充。"
        )
    schedulable = [
        course
        for course in required
        if course.matched_course_id and course.eligible_section_count > 0
    ]
    if not schedulable:
        detail = "；".join(warnings)
        raise CurriculumError(
            "该学期培养方案没有可安全自动排入的必修课。"
            f"{detail}"
        )
    if warnings:
        warnings.insert(
            0,
            f"本次为部分培养方案排课：已自动提交 {len(schedulable)} 门可安全匹配的必修课；"
            "下列项目仍需你处理。",
        )
    return CurriculumResolution(
        choices=tuple(
            CourseChoice(
                course_id=course.matched_course_id or "",
                priority=200,
                required=True,
            )
            for course in schedulable
        ),
        warnings=tuple(warnings),
    )


def validate_curriculum_selection(
    db: Session,
    profile: StudentProfile,
    selection: CurriculumSelection,
) -> CurriculumPreviewResponse:
    if not selection.confirmed_by_user:
        raise CurriculumError("载入培养方案前必须确认实际所在学期")
    semester = selection.semester or profile.semester_override
    if semester is None:
        semester = 2 * (2026 - profile.cohort_year) + 1
    preview = preview_for_profile(db, profile, semester=semester)
    if preview.manual_only or preview.source is None:
        raise CurriculumError("该学院、专业、年级、班型和合作项目当前只能使用手动输入")
    if not selection.source_id:
        raise CurriculumError("培养方案或混合模式必须携带已核验的官方来源 ID")
    if selection.source_id != preview.source.id:
        raise CurriculumError("请求的培养方案与已保存的学生身份不匹配")
    return preview


def mixed_curriculum_warnings(
    preview: CurriculumPreviewResponse,
    choices: list[CourseChoice],
) -> list[str]:
    """Describe every curriculum item a mixed-mode edit leaves unresolved."""

    selected_ids = {choice.course_id for choice in choices}
    warnings: list[str] = []
    unmatched = [
        course for course in preview.courses if course.required and not course.matched_course_id
    ]
    if unmatched:
        warnings.append(
            "培养方案必修课仍有课程总库未匹配项："
            + "、".join(f"{course.code} {course.name}" for course in unmatched)
            + "。请到教务系统核对，并手动搜索同名/替代课程；本软件不会把它们视为已完成。"
        )
    removed = [
        course
        for course in preview.courses
        if course.required
        and course.matched_course_id
        and course.matched_course_id not in selected_ids
    ]
    if removed:
        warnings.append(
            "你在混合编辑中未提交以下已匹配必修课："
            + "、".join(f"{course.code} {course.name}" for course in removed)
            + "。这可能是有意删改，也可能是遗漏，请在最终选课前逐门确认。"
        )
    for group in sorted(
        {
            course.selection_group or "未命名方向组"
            for course in preview.courses
            if course.requirement_type == "track_choice"
        }
    ):
        candidates = [
            course
            for course in preview.courses
            if course.requirement_type == "track_choice"
            and (course.selection_group or "未命名方向组") == group
        ]
        if not any(
            course.matched_course_id in selected_ids
            for course in candidates
            if course.matched_course_id
        ):
            warnings.append(
                f"培养方案方向/课程组“{group}”尚未选择可排课程；请核对方向要求。"
            )
    return warnings


def _courses_for_semester(
    db: Session, plan: dict[str, Any], semester: int
) -> tuple[list[CurriculumCourseResponse], list[str]]:
    records = [
        record
        for record in plan.get("courses", [])
        if isinstance(record, dict)
        and _recommended_in_semester(record, semester)
        and record.get("code")
        and record.get("name")
    ]
    codes = sorted({str(record["code"]) for record in records})
    catalog_by_code: dict[str, list[CatalogCourse]] = {}
    if codes:
        for course in db.scalars(
            select(CatalogCourse)
            .options(selectinload(CatalogCourse.sections))
            .where(CatalogCourse.code.in_(codes))
        ):
            catalog_by_code.setdefault(course.code, []).append(course)

    warnings: list[str] = []
    responses: list[CurriculumCourseResponse] = []
    for record in records:
        code = str(record["code"])
        name = str(record["name"])
        candidates = catalog_by_code.get(code, [])
        matched_id: str | None = None
        matched_course: CatalogCourse | None = None
        match_state = "unmatched"
        if candidates:
            exact = next(
                (
                    candidate
                    for candidate in candidates
                    if _normalize(candidate.name) == _normalize(name)
                ),
                None,
            )
            if exact is not None:
                matched_id = exact.id
                matched_course = exact
                match_state = "exact_code_and_name"
            elif len(candidates) == 1 and ratio(name, candidates[0].name) >= 80:
                matched_id = candidates[0].id
                matched_course = candidates[0]
                match_state = "code_match_name_variant"
            else:
                match_state = "ambiguous_code"
        requirement_type = str(record.get("required_or_elective") or "")
        required = requirement_type == "required"
        sections = matched_course.sections if matched_course is not None else []
        unknown_precisions = {"week_only", "date_range", "tbd"}
        has_unknown_time = {
            section.id: any(
                meeting.get("precision") in unknown_precisions
                for meeting in section.meetings
            )
            for section in sections
        }
        is_legacy_only = {
            section.id: any(
                issue.get("code") == "old_snapshot_only"
                for issue in section.import_issues
            )
            for section in sections
        }
        responses.append(
            CurriculumCourseResponse(
                code=code,
                name=name,
                semester=semester,
                credits=_optional_float(record.get("credits")),
                required=required,
                matched_course_id=matched_id,
                match_state=match_state,
                category=_optional_string(record.get("category")),
                requirement_type=requirement_type or None,
                selection_group=_selection_group_label(record.get("selection_group")),
                selection_rule=_optional_string(record.get("selection_rule")),
                source_page=_optional_int(record.get("source_page")),
                section_count=len(sections),
                eligible_section_count=sum(section.default_eligible for section in sections),
                confirmation_required_section_count=sum(
                    section.needs_confirmation for section in sections
                ),
                legacy_only_section_count=sum(is_legacy_only.values()),
                data_quality_confirmation_section_count=sum(
                    section.needs_confirmation
                    and not is_legacy_only[section.id]
                    and not has_unknown_time[section.id]
                    for section in sections
                ),
                unknown_time_section_count=sum(has_unknown_time.values()),
            )
        )
    unmatched_count = sum(course.matched_course_id is None for course in responses)
    if unmatched_count:
        warnings.append(
            f"本学期有 {unmatched_count} 条培养方案课程未能与课程总库精确匹配，"
            "不会自动编造教学班。"
        )
    optional_count = sum(not course.required for course in responses)
    if optional_count:
        warnings.append(
            f"本学期另有 {optional_count} 条选修、方向或课程组候选；"
            "纯培养方案模式不会把它们全部当作必修，混合模式可逐门选择。"
        )
    return responses, warnings


def _source_response(source: dict[str, Any]) -> CurriculumSourceResponse:
    applicable = source.get("applicable_cohorts") or {}
    cohort = applicable.get("from") if applicable.get("from") == applicable.get("through") else None
    access_level = str(source.get("access_level") or "D")
    return CurriculumSourceResponse(
        id=str(source.get("id")),
        college=str(source.get("college_name")),
        major=str(source.get("major")),
        cohort_year=_optional_int(cohort),
        plan_variant=_optional_string(source.get("variant")),
        status=f"{access_level} · {source.get('status')}",
        official_url=_optional_string(source.get("landing_url")),
        document_url=_optional_string(source.get("direct_url")),
        checked_at=_optional_string(source.get("retrieved_at")),
        note=_optional_string(source.get("notes") or source.get("evidence")),
        supports_import=access_level == "A",
    )


def _manual_only_message(access_level: str, source: dict[str, Any]) -> str:
    if access_level == "B":
        return "官网找到了附件，但下载需要验证码；上传并核验原件前只能手动输入。"
    if access_level == "C":
        return "官网正文需要学校身份认证；软件不会收集账号密码，取得正式导出前只能手动输入。"
    return str(source.get("notes") or "未找到当前完整培养方案，只能手动输入。")


def _cohort_applies(source: dict[str, Any], cohort: int) -> bool:
    value = source.get("applicable_cohorts") or {}
    start = _optional_int(value.get("from"))
    end = _optional_int(value.get("through"))
    return (start is None or cohort >= start) and (end is None or cohort <= end)


def _canonical_college(value: str) -> str:
    normalized = _normalize(value)
    for alias, canonical in _COLLEGE_ALIASES.items():
        if normalized == _normalize(alias):
            return _normalize(canonical)
    return normalized


def _is_cooperative_profile(value: str | None) -> bool:
    normalized = _normalize(value or "无")
    return normalized not in {"", "无", "否", "none", "不涉及", "普通"}


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(text.split()).casefold().replace("（", "(").replace("）", ")")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _selection_group_label(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _optional_string(value.get("name") or value.get("id") or value.get("type"))
    return str(value)


def _recommended_in_semester(record: dict[str, Any], semester: int) -> bool:
    value = record.get("recommended_semester")
    if not isinstance(value, list):
        return False
    return semester in value
