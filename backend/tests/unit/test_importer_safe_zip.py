from __future__ import annotations

import struct
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.importers import (
    ZipSafetyLimits,
    decode_legacy_zip_name,
    read_safe_zip_entries,
    safe_windows_filename,
    validate_member_path,
)


def test_member_path_validation_rejects_traversal_and_windows_absolute_paths() -> None:
    assert validate_member_path("normal/course.xls") is None
    assert validate_member_path("../escape.xls") is not None
    assert validate_member_path(r"folder\..\escape.xls") is not None
    assert validate_member_path("/absolute.xls") is not None
    assert validate_member_path(r"C:\absolute.xls") is not None


def test_windows_illegal_filename_is_sanitized_with_collision_suffix() -> None:
    original = "22314120-传感器原理II: 现代传感器技术(20260823).xls"
    safe = safe_windows_filename(original)

    assert ":" not in safe
    assert safe.endswith(".xls")
    assert safe != safe_windows_filename(original.replace(":", "_"))


def test_gbk_member_name_is_recovered_and_read_without_extraction(tmp_path: Path) -> None:
    filename = "20706100-机械工程控制基础(20260823).xls"
    archive = tmp_path / "legacy-gbk.zip"
    archive.write_bytes(_stored_zip_with_raw_name(filename.encode("gbk"), b"xls-bytes"))

    entries, issues = read_safe_zip_entries(archive, snapshot_id="legacy")

    assert issues == ()
    assert len(entries) == 1
    assert entries[0].source.original_entry_name == filename
    assert entries[0].data == b"xls-bytes"
    assert not (tmp_path / filename).exists()


def test_unsafe_zip_member_is_reported_and_never_returned(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zipped:
        zipped.writestr("../escape.xls", b"bad")
        zipped.writestr("safe:course.xls", b"good")

    entries, issues = read_safe_zip_entries(archive, snapshot_id="snapshot")

    assert [entry.source.original_entry_name for entry in entries] == ["safe:course.xls"]
    assert ":" not in entries[0].source.safe_filename
    assert [issue.code for issue in issues] == ["unsafe_archive_member_path"]
    assert not (tmp_path.parent / "escape.xls").exists()


def test_per_entry_size_limit_skips_member(tmp_path: Path) -> None:
    archive = tmp_path / "large.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("large.xls", b"12345")

    entries, issues = read_safe_zip_entries(
        archive,
        snapshot_id="snapshot",
        limits=ZipSafetyLimits(max_entry_bytes=4),
    )

    assert entries == ()
    assert [issue.code for issue in issues] == ["archive_member_too_large"]


def test_cp437_to_gbk_recovery_is_lossless() -> None:
    expected = "课程课表.xls"
    mojibake = expected.encode("gbk").decode("cp437")

    assert decode_legacy_zip_name(mojibake, utf8_flag=False) == expected
    assert decode_legacy_zip_name(expected, utf8_flag=True) == expected


def _stored_zip_with_raw_name(raw_name: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(payload)
    local = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(payload),
        len(payload),
        len(raw_name),
        0,
    )
    central = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(payload),
        len(payload),
        len(raw_name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    local_record = local + raw_name + payload
    central_record = central + raw_name
    end = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        1,
        1,
        len(central_record),
        len(local_record),
        0,
    )
    return local_record + central_record + end
