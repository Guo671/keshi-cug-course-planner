from __future__ import annotations

import hashlib
from typing import Any

import pytest
from app.importers import (
    OLE2_SIGNATURE,
    Confidence,
    SourceDocument,
    TimePrecision,
    WorkbookImportError,
    parse_schedule_matrix,
    parse_week_expression,
    parse_workbook_bytes,
)

NOTE = (
    "注--内容顺序为：课程<>周次<>校区<>地点<>教师<>教学班<>教学班组成<>"
    "选课人数<>考核方式本学期2026-08-31正式上课至2027-01-24结束，共21周. "
    "打印时间：2026-08-23"
)


def test_week_expression_expands_segments_parity_and_period_override() -> None:
    parsed = parse_week_expression("(3-3节)1-7周(单),8-10周")

    assert parsed.explicit_periods == (3, 3)
    assert parsed.weeks == (1, 3, 5, 7, 8, 9, 10)
    assert parsed.confidence is Confidence.HIGH
    assert parsed.issues == ()


def test_invalid_week_segment_is_retained_as_issue() -> None:
    parsed = parse_week_expression("1-4周,22周", total_weeks=21)

    assert parsed.weeks == (1, 2, 3, 4)
    assert parsed.confidence is Confidence.LOW
    assert [issue.code for issue in parsed.issues] == ["week_expression_invalid"]


def test_invalid_explicit_period_makes_section_unreliable() -> None:
    source = _source("new", "20700000-测试课程(123).xls")
    matrix = _matrix("20700000", "测试课程", "测试学院")
    matrix[2][2] = "测试教师/(4-3节)1-4周/南望山校区 东教楼A0101/测试课程-0001/072241/1/考试"

    section = parse_schedule_matrix(matrix, source=source).teaching_classes[0]

    assert not section.reliable_for_scheduling
    assert section.needs_confirmation
    assert "period_expression_invalid" in {issue.code for issue in section.issues}


def test_ordinary_parser_handles_slash_in_course_name_and_never_infers_capacity() -> None:
    source = _source("new", "20446200-水文地球化学-附水分析(123).xls")
    matrix = _matrix("20446200", "水文地球化学/附水分析", "环境学院")
    matrix[3][4] = (
        "郭清海/12-18周/未来城校区 公教1-413/水文地球化学/附水分析-0001/045241z/26/未安排"
    )

    course = parse_schedule_matrix(matrix, source=source)
    section = course.teaching_classes[0]
    meeting = section.meetings[0]

    assert section.section_code == "0001"
    assert section.class_label_raw == "水文地球化学/附水分析-0001"
    assert meeting.campus == "未来城校区"
    assert meeting.room == "公教1-413"
    assert meeting.cell.a1 == "E4"
    assert meeting.raw == matrix[3][4]
    assert meeting.weeks == tuple(range(12, 19))
    assert section.enrolled_count_snapshot == 26
    assert section.capacity is None
    assert meeting.capacity is None


def test_ordinary_parser_handles_slash_in_room_and_explicit_single_period() -> None:
    source = _source("new", "20300000-化学实验(123).xls")
    matrix = _matrix("20300000", "化学实验", "材料与化学学院")
    matrix[2][2] = "张老师/(3-3节)2-4周/南望山校区 化学楼103/104/化学实验-0002/031241/7/考查"

    course = parse_schedule_matrix(matrix, source=source)
    meeting = course.teaching_classes[0].meetings[0]

    assert meeting.location_raw == "南望山校区 化学楼103/104"
    assert meeting.room == "化学楼103/104"
    assert (meeting.start_period, meeting.end_period) == (3, 3)
    assert meeting.weekday == 1


def test_same_section_number_with_different_composition_does_not_collide() -> None:
    source = _source("new", "20860000-投资学(123).xls")
    matrix = _matrix("20860000", "投资学", "经济管理学院")
    matrix[2][2] = "徐翔/1-3周/南望山校区 东教楼B0203/2025辅修-投资学-0001/2025经济学/0/考查"
    matrix[4][4] = "徐翔/10-17周/未来城校区 公教1-519/投资学-0001/081241;081242;081243/80/考试"

    course = parse_schedule_matrix(matrix, source=source)

    assert len(course.teaching_classes) == 2
    assert {section.section_code for section in course.teaching_classes} == {"0001"}
    assert {section.class_composition_raw for section in course.teaching_classes} == {
        "2025经济学",
        "081241;081242;081243",
    }


def test_section_code_is_recovered_before_human_qualifier() -> None:
    source = _source("new", "21225412-大学物理B1(123).xls")
    matrix = _matrix("21225412", "大学物理B1", "数理学院")
    matrix[2][2] = (
        "郑安寿/2-5周/南望山校区 教三楼406/大学物理B1-0001（预科班）/12y261;12y262/0/未安排"
    )

    section = parse_schedule_matrix(matrix, source=source).teaching_classes[0]

    assert section.section_code == "0001"
    assert section.confidence is Confidence.HIGH


def test_concentrated_practice_retains_weeks_but_is_not_reliable() -> None:
    source = _source("new", "40118500-毕业生产实习(123).xls")
    matrix = _matrix("40118500", "毕业生产实习", "地球与行星科学学院", practice=True)
    matrix[8][0] = (
        "实践课程：毕业生产实习赵璐璐(共8周)/1-8周/"
        "毕业生产实习-地表/010231;010232;   "
        "毕业生产实习徐旺春(共8周)/1-8周/"
        "毕业生产实习-地化/010231;010232;   "
    )

    course = parse_schedule_matrix(matrix, source=source)

    assert len(course.teaching_classes) == 2
    for section in course.teaching_classes:
        assert section.section_code is None
        assert section.needs_confirmation
        assert not section.reliable_for_scheduling
        assert section.capacity is None
        meeting = section.meetings[0]
        assert meeting.precision is TimePrecision.WEEK_ONLY
        assert meeting.weeks == tuple(range(1, 9))
        assert meeting.weekday is None
        assert "practice_time_unknown" in {issue.code for issue in meeting.issues}
        assert meeting.raw
        assert meeting.source is source


def test_parse_workbook_bytes_uses_in_memory_xlrd_and_releases_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source("new", "20706100-机械工程控制基础(123).xls")
    matrix = _matrix("20706100", "机械工程控制基础", "机械与电子信息学院")
    released = False

    class FakeSheet:
        name = "Sheet0"
        nrows = len(matrix)
        ncols = 9

        @staticmethod
        def cell_value(row: int, column: int) -> Any:
            return matrix[row][column]

    class FakeBook:
        nsheets = 1

        @staticmethod
        def sheet_by_index(index: int) -> FakeSheet:
            assert index == 0
            return FakeSheet()

        @staticmethod
        def release_resources() -> None:
            nonlocal released
            released = True

    def fake_open_workbook(*, file_contents: bytes, on_demand: bool) -> FakeBook:
        assert file_contents.startswith(OLE2_SIGNATURE)
        assert on_demand
        return FakeBook()

    monkeypatch.setattr("xlrd.open_workbook", fake_open_workbook)
    parsed = parse_workbook_bytes(OLE2_SIGNATURE + b"fixture", source=source)

    assert parsed.course_code == "20706100"
    assert released


def test_parse_workbook_bytes_rejects_non_ole_input() -> None:
    with pytest.raises(WorkbookImportError, match="OLE2"):
        parse_workbook_bytes(b"not an xls", source=_source("bad", "bad.xls"))


def _source(snapshot: str, filename: str) -> SourceDocument:
    digest = hashlib.sha256(filename.encode()).hexdigest()
    return SourceDocument(
        snapshot_id=snapshot,
        kind="zip_entry",
        container="fixture.zip",
        original_entry_name=filename,
        safe_filename=filename,
        sha256=digest,
        size_bytes=1,
    )


def _matrix(
    code: str,
    name: str,
    college: str,
    *,
    practice: bool = False,
) -> list[list[str]]:
    rows = 10 if practice else 9
    matrix = [[""] * 9 for _ in range(rows)]
    matrix[0][0] = "2026-2027学年第1学期"
    matrix[0][3] = f"{name}课程课表"
    matrix[0][7] = f"{code} {college}"
    matrix[1] = ["节次", "", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    matrix[2][0:2] = ["上午", "一"]
    matrix[3][0:2] = ["上午", "二"]
    matrix[4][0:2] = ["下午", "三"]
    matrix[5][0:2] = ["下午", "四"]
    matrix[6][0:2] = ["晚上", "五"]
    matrix[7][0:2] = ["晚上", "六"]
    matrix[-1][0] = NOTE
    return matrix
