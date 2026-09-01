"""Validate curricula JSON files against JSON Schema and semantic invariants."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:  # pragma: no cover - explicit operator guidance
    raise SystemExit(
        "缺少 jsonschema。请在临时环境安装后运行：python -m pip install jsonschema"
    ) from exc


ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / "schema"
PLANS = ROOT / "plans"
RETRIEVED = "2026-08-23"

EXPECTED_ACCESS = {
    "A": ("direct", "EXACT_VERIFIED"),
    "B": ("captcha/upload_required", "FOUND_UNPARSED"),
    "C": ("auth_required", "LOCKED"),
    "D": ("not_found/manual_only", "MISSING"),
}
EXPECTED_NEW_2026 = {
    "行星科学",
    "空间科学与技术",
    "机器人工程",
    "人工智能",
    "数字经济",
    "智慧城市与空间规划",
}
EXPECTED_PLAN_COUNTS = {
    "cug-au-080301-2023": 68,
    "cug-au-080801-2023": 79,
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_validate(instance: dict, schema_path: Path, label: str) -> None:
    schema = load(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if errors:
        lines = []
        for error in errors:
            location = "/".join(str(part) for part in error.absolute_path) or "<root>"
            lines.append(f"{label}:{location}: {error.message}")
        raise AssertionError("\n".join(lines))


def validate_registry(registry: dict) -> None:
    sources = registry["sources"]
    assert registry["retrieved_at"] == RETRIEVED
    assert registry["scope"]["registry_entry_count"] == len(sources)
    assert len({source["id"] for source in sources}) == len(sources), "source id不唯一"
    assert all(source["retrieved_at"] == RETRIEVED for source in sources)

    for source in sources:
        expected_mode, expected_status = EXPECTED_ACCESS[source["access_level"]]
        assert source["access_mode"] == expected_mode
        assert source["status"] == expected_status
        if source["access_level"] == "A":
            assert source["direct_url"], f"A类缺少direct_url: {source['id']}"
        else:
            assert source["direct_url"] is None, f"非A类不应有direct_url: {source['id']}"
        cohorts = source["applicable_cohorts"]
        if cohorts["from"] is not None and cohorts["through"] is not None:
            assert cohorts["from"] <= cohorts["through"], f"年级范围反向: {source['id']}"

    current_records = {
        (source["major_code"], source["major"])
        for source in sources
        if source["catalog_current_2026"] and source["major_code"]
    }
    reported = registry["scope"]["current_catalog_major_count_reported"]
    stored = registry["scope"]["current_catalog_unique_major_records_in_registry"]
    assert reported == 76
    assert stored == 76
    assert len(current_records) == 76

    new_rows = [
        source
        for source in sources
        if source["major"] in EXPECTED_NEW_2026 and source["catalog_current_2026"]
    ]
    assert {row["major"] for row in new_rows} == EXPECTED_NEW_2026
    assert all(row["first_enrollment_year"] == 2026 for row in new_rows)
    assert all(row["access_level"] == "D" for row in new_rows)

    a_rows = [source for source in sources if source["access_level"] == "A"]
    assert {(row["major_code"], row["major"]) for row in a_rows} == {
        ("080301", "测控技术与仪器"),
        ("080801", "自动化"),
    }


def validate_plan(plan: dict) -> None:
    courses = plan["courses"]
    issues = plan["issues"]
    issue_ids = {issue["id"] for issue in issues}
    assert len(issue_ids) == len(issues), f"{plan['plan_id']}: issue id不唯一"
    assert plan["source"]["retrieved_at"] == RETRIEVED
    assert plan["source"]["official_pdf_committed"] is False
    assert plan["verification"]["course_record_count"] == len(courses)
    assert len(courses) == EXPECTED_PLAN_COUNTS[plan["plan_id"]]
    assert plan["semester_mapping_2026_fall"]["requires_user_confirmation"] is True

    table_pages = set(plan["source"]["course_table_pages"])
    non_null_codes = [course["code"] for course in courses if course["code"] is not None]
    duplicates = [code for code, count in Counter(non_null_codes).items() if count > 1]
    assert not duplicates, f"{plan['plan_id']}: 重复课程号 {duplicates}"

    for index, item in enumerate(courses):
        assert item["source_page"] in table_pages, (
            f"{plan['plan_id']}: courses[{index}] source_page不在课程表页"
        )
        assert set(item["issue_ids"]) <= issue_ids, (
            f"{plan['plan_id']}: courses[{index}]引用不存在的issue"
        )
        semesters = item["recommended_semester"]
        distribution = item["semester_credit_distribution"]
        if distribution:
            distributed_semesters = {int(key) for key in distribution}
            assert semesters is not None
            assert distributed_semesters <= set(semesters)
            distributed_credits = sum(distribution.values())
            if not math.isclose(distributed_credits, item["credits"], abs_tol=1e-9):
                assert item["issue_ids"], (
                    f"{plan['plan_id']} {item['code']}: 学期学分和总学分不一致但未登记issue"
                )

    if plan["plan_id"] == "cug-au-080301-2023":
        conflict = next(item for item in courses if item["code"] == "22315100")
        assert conflict["credits"] == 2
        assert conflict["semester_credit_distribution"] == {"7": 1.5}
        assert conflict["issue_ids"] == ["measurement-iot-credit-conflict"]


def main() -> None:
    registry = load(ROOT / "source_registry.json")
    schema_validate(
        registry,
        SCHEMA / "source_registry.schema.json",
        "source_registry.json",
    )
    validate_registry(registry)

    plans = []
    for path in sorted(PLANS.glob("*.json")):
        instance = load(path)
        schema_validate(instance, SCHEMA / "curriculum_plan.schema.json", path.name)
        validate_plan(instance)
        plans.append(instance)

    assert {plan["plan_id"] for plan in plans} == set(EXPECTED_PLAN_COUNTS)
    levels = Counter(source["access_level"] for source in registry["sources"])
    colleges = {source["college_id"] for source in registry["sources"]}
    print("PASS: JSON parse + Draft 2020-12 schema + semantic invariants")
    print(
        "registry:",
        f"{len(registry['sources'])} entries,",
        f"{len(colleges)} colleges/special units,",
        "76 current catalog majors,",
        "levels",
        dict(sorted(levels.items())),
    )
    for plan in plans:
        print(
            f"plan {plan['major']} ({plan['major_code']}):",
            f"{len(plan['courses'])} course records,",
            f"{len(plan['issues'])} documented issues/constraints",
        )


if __name__ == "__main__":
    main()
