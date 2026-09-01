"""Read schedule archives without ever extracting untrusted member paths."""

from __future__ import annotations

import hashlib
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo

from .models import ImportIssue, IssueSeverity, SourceDocument


class UnsafeArchiveError(ValueError):
    """Raised when an archive-wide safety limit is exceeded."""


@dataclass(frozen=True, slots=True)
class ZipSafetyLimits:
    max_entries: int = 2_500
    max_entry_bytes: int = 16 * 1024 * 1024
    max_total_uncompressed_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: float = 500.0


DEFAULT_ZIP_SAFETY_LIMITS = ZipSafetyLimits()


@dataclass(frozen=True, slots=True)
class SafeZipEntry:
    source: SourceDocument
    data: bytes


_WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def decode_legacy_zip_name(filename: str, *, utf8_flag: bool) -> str:
    """Recover a GBK member name decoded by ``zipfile`` as CP437.

    ZIP has no mandatory filename encoding for entries without bit 11.  The
    legacy CUG archives were produced on Chinese Windows and use GBK.  CP437 is
    reversible, so decoding the original bytes as GBK is lossless when valid.
    """

    if utf8_flag:
        return filename
    try:
        raw_name = filename.encode("cp437")
        return raw_name.decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return filename


def validate_member_path(name: str) -> str | None:
    """Return a rejection reason for an unsafe archive path, otherwise None."""

    if not name or "\x00" in name:
        return "empty or NUL-containing member name"
    canonical = name.replace("\\", "/")
    if canonical.startswith("/") or canonical.startswith("//"):
        return "absolute member path"
    if re.match(r"^[A-Za-z]:", canonical):
        return "drive-qualified member path"
    parts = canonical.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return "empty, current-directory, or parent-directory path component"
    # PurePosixPath is used only for lexical validation; no member is extracted.
    if PurePosixPath(canonical).is_absolute():
        return "absolute member path"
    return None


def safe_windows_filename(name: str) -> str:
    """Return a collision-resistant Windows-safe basename for display/export."""

    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _WINDOWS_INVALID.sub("_", basename).rstrip(" .")
    if not cleaned:
        cleaned = "unnamed"
    stem = cleaned.rsplit(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    changed = cleaned != basename
    if len(cleaned) > 220:
        changed = True
        suffix = Path(cleaned).suffix[:20]
        cleaned = f"{Path(cleaned).stem[:180]}{suffix}"
    if changed:
        digest = hashlib.sha256(basename.encode("utf-8", "surrogatepass")).hexdigest()[:8]
        path = Path(cleaned)
        cleaned = f"{path.stem}~{digest}{path.suffix}"
    return cleaned


def read_safe_zip_entries(
    archive_path: str | Path,
    *,
    snapshot_id: str,
    limits: ZipSafetyLimits = DEFAULT_ZIP_SAFETY_LIMITS,
    suffixes: tuple[str, ...] = (".xls",),
) -> tuple[tuple[SafeZipEntry, ...], tuple[ImportIssue, ...]]:
    """Read approved members into memory, never writing member names to disk."""

    archive = Path(archive_path)
    entries: list[SafeZipEntry] = []
    issues: list[ImportIssue] = []
    try:
        with ZipFile(archive) as zipped:
            infos = zipped.infolist()
            if len(infos) > limits.max_entries:
                raise UnsafeArchiveError(
                    f"archive has {len(infos)} entries; limit is {limits.max_entries}"
                )
            total_size = 0
            for info in infos:
                decoded_name = decode_legacy_zip_name(
                    info.filename, utf8_flag=bool(info.flag_bits & 0x800)
                )
                if info.is_dir():
                    continue
                reason = validate_member_path(decoded_name)
                if reason is not None:
                    issues.append(
                        ImportIssue(
                            code="unsafe_archive_member_path",
                            message=f"Skipped {decoded_name!r}: {reason}",
                            severity=IssueSeverity.ERROR,
                        )
                    )
                    continue
                if _is_symlink(info):
                    issues.append(
                        ImportIssue(
                            code="archive_symlink_skipped",
                            message=f"Skipped symbolic-link member {decoded_name!r}",
                            severity=IssueSeverity.ERROR,
                        )
                    )
                    continue
                if info.flag_bits & 0x1:
                    issues.append(
                        ImportIssue(
                            code="encrypted_archive_member_skipped",
                            message=f"Skipped encrypted member {decoded_name!r}",
                            severity=IssueSeverity.ERROR,
                        )
                    )
                    continue
                if suffixes and not decoded_name.casefold().endswith(
                    tuple(s.casefold() for s in suffixes)
                ):
                    continue
                if info.file_size > limits.max_entry_bytes:
                    issues.append(
                        ImportIssue(
                            code="archive_member_too_large",
                            message=f"Skipped {decoded_name!r}: {info.file_size} bytes",
                            severity=IssueSeverity.ERROR,
                        )
                    )
                    continue
                total_size += info.file_size
                if total_size > limits.max_total_uncompressed_bytes:
                    raise UnsafeArchiveError(
                        "archive uncompressed size exceeds "
                        f"{limits.max_total_uncompressed_bytes} bytes"
                    )
                compressed = max(info.compress_size, 1)
                if info.file_size / compressed > limits.max_compression_ratio:
                    issues.append(
                        ImportIssue(
                            code="suspicious_compression_ratio",
                            message=f"Skipped {decoded_name!r}: suspicious compression ratio",
                            severity=IssueSeverity.ERROR,
                        )
                    )
                    continue
                data = zipped.read(info)
                if len(data) != info.file_size:
                    issues.append(
                        ImportIssue(
                            code="archive_member_size_mismatch",
                            message=f"Skipped {decoded_name!r}: decompressed size mismatch",
                            severity=IssueSeverity.ERROR,
                        )
                    )
                    continue
                source = SourceDocument(
                    snapshot_id=snapshot_id,
                    kind="zip_entry",
                    container=str(archive),
                    original_entry_name=decoded_name,
                    safe_filename=safe_windows_filename(decoded_name),
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                )
                entries.append(SafeZipEntry(source=source, data=data))
    except BadZipFile as exc:
        raise UnsafeArchiveError(f"invalid ZIP archive: {archive}") from exc
    return tuple(entries), tuple(issues)


def _is_symlink(info: ZipInfo) -> bool:
    unix_mode = info.external_attr >> 16
    return stat.S_ISLNK(unix_mode)
