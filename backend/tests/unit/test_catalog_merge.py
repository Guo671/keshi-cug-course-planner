from __future__ import annotations

import hashlib

from app.importers import (
    CatalogSnapshot,
    MergeDisposition,
    SourceDocument,
    merge_snapshots,
    parse_schedule_matrix,
)

NOTE = "注：本学期2026-08-31正式上课至2027-01-24结束，共21周. 打印时间：2026-08-23"


def test_new_snapshot_wins_and_enrollment_count_is_not_treated_as_capacity() -> None:
    new = _snapshot("new", (("0001", "50"),))
    old = _snapshot("old", (("0001", "0"), ("0002", "10")))

    merged = merge_snapshots(new, old)
    same = next(item for item in merged.teaching_classes if item.selected.section_code == "0001")
    old_only = next(
        item for item in merged.teaching_classes if item.selected.section_code == "0002"
    )

    assert same.disposition is MergeDisposition.NEW_PREFERRED
    assert same.selected.source.snapshot_id == "new"
    assert same.selected.enrolled_count_snapshot == 50
    assert same.selected.capacity is None
    assert len(same.versions) == 2
    assert {issue.code for issue in same.issues} == {"enrollment_snapshot_updated"}
    assert old_only.disposition is MergeDisposition.OLD_ONLY
    assert old_only.needs_confirmation
    assert not old_only.reliable_for_scheduling
    assert not old_only.eligible_for_scheduling
    assert old_only.selected not in merged.scheduling_candidates


def test_explicit_opt_in_admits_exact_old_only_but_does_not_make_it_reliable() -> None:
    new = _snapshot("new", (("0001", "50"),))
    old = _snapshot("old", (("0002", "10"),))

    merged = merge_snapshots(new, old, include_old_only=True)
    old_only = next(
        item for item in merged.teaching_classes if item.disposition is MergeDisposition.OLD_ONLY
    )

    assert old_only.eligible_for_scheduling
    assert not old_only.reliable_for_scheduling
    assert old_only.needs_confirmation
    assert old_only.selected in merged.scheduling_candidates


def test_structural_change_is_reported_while_new_version_remains_selected() -> None:
    new = _snapshot("new", (("0001", "50"),), room="东教楼A0101")
    old = _snapshot("old", (("0001", "0"),), room="东教楼A0102")

    merged = merge_snapshots(new, old)
    section = merged.teaching_classes[0]

    assert section.selected.meetings[0].room == "东教楼A0101"
    assert {issue.code for issue in section.issues} == {"snapshot_structure_changed"}
    assert merged.statistics.structurally_changed == 1
    assert merged.statistics.enrollment_only_changed == 0


def test_class_composition_order_is_not_part_of_teaching_class_identity() -> None:
    new = _snapshot("new", (("0001", "50"),), composition="072241;072242")
    old = _snapshot("old", (("0001", "0"),), composition="072242;072241")

    merged = merge_snapshots(new, old)

    assert len(merged.teaching_classes) == 1
    assert merged.teaching_classes[0].disposition is MergeDisposition.NEW_PREFERRED
    assert merged.statistics.structurally_changed == 0
    assert merged.statistics.enrollment_only_changed == 1


def _snapshot(
    snapshot_id: str,
    sections: tuple[tuple[str, str], ...],
    *,
    room: str = "东教楼A0101",
    composition: str = "072241",
) -> CatalogSnapshot:
    source = _source(snapshot_id)
    matrix = [[""] * 9 for _ in range(9)]
    matrix[0][0] = "2026-2027学年第1学期"
    matrix[0][3] = "测试课程课程课表"
    matrix[0][7] = "20700000 测试学院"
    matrix[1] = ["节次", "", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    for index, (section_code, count) in enumerate(sections):
        matrix[2 + index][2] = (
            f"测试教师/1-8周/南望山校区 {room}/测试课程-{section_code}/{composition}/{count}/考试"
        )
    matrix[8][0] = NOTE
    course = parse_schedule_matrix(matrix, source=source)
    return CatalogSnapshot(snapshot_id=snapshot_id, courses=(course,))


def _source(snapshot_id: str) -> SourceDocument:
    return SourceDocument(
        snapshot_id=snapshot_id,
        kind="file",
        container=f"{snapshot_id}.xls",
        original_entry_name=None,
        safe_filename="20700000-测试课程(123).xls",
        sha256=hashlib.sha256(snapshot_id.encode()).hexdigest(),
        size_bytes=1,
    )
