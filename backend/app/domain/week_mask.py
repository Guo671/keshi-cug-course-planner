"""Compact, immutable representation of academic teaching weeks.

Week 1 is stored in the least-significant bit.  The representation makes the
most common conflict check -- ``a.bits & b.bits`` -- both explicit and cheap,
while keeping construction and validation out of importer and solver code.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

MAX_ACADEMIC_WEEK = 64


class WeekParity(StrEnum):
    """Parity filter used by schedules such as ``5-19周(单)``."""

    ALL = "all"
    ODD = "odd"
    EVEN = "even"


@dataclass(frozen=True, slots=True)
class WeekMask:
    """A validated set of one-based academic week numbers."""

    bits: int

    def __post_init__(self) -> None:
        if not isinstance(self.bits, int):
            raise TypeError("WeekMask.bits must be an int")
        if self.bits < 0:
            raise ValueError("WeekMask.bits cannot be negative")
        if self.bits.bit_length() > MAX_ACADEMIC_WEEK:
            raise ValueError(f"academic week cannot exceed {MAX_ACADEMIC_WEEK}")

    @classmethod
    def empty(cls) -> WeekMask:
        return cls(0)

    @classmethod
    def all(cls, total_weeks: int) -> WeekMask:
        _validate_week(total_weeks)
        return cls((1 << total_weeks) - 1)

    @classmethod
    def from_weeks(cls, weeks: Iterable[int]) -> WeekMask:
        bits = 0
        for week in weeks:
            _validate_week(week)
            bits |= 1 << (week - 1)
        return cls(bits)

    @classmethod
    def from_range(
        cls,
        start: int,
        end: int,
        parity: WeekParity = WeekParity.ALL,
    ) -> WeekMask:
        _validate_week(start)
        _validate_week(end)
        if start > end:
            raise ValueError("start week cannot be after end week")
        try:
            resolved_parity = WeekParity(parity)
        except ValueError as exc:
            raise ValueError(f"unsupported week parity: {parity!r}") from exc

        weeks: Iterable[int] = range(start, end + 1)
        if resolved_parity is WeekParity.ODD:
            weeks = (week for week in weeks if week % 2 == 1)
        elif resolved_parity is WeekParity.EVEN:
            weeks = (week for week in weeks if week % 2 == 0)
        return cls.from_weeks(weeks)

    @property
    def weeks(self) -> tuple[int, ...]:
        return tuple(iter(self))

    def contains(self, week: int) -> bool:
        _validate_week(week)
        return bool(self.bits & (1 << (week - 1)))

    def intersects(self, other: WeekMask) -> bool:
        return bool(self.bits & other.bits)

    def intersection(self, other: WeekMask) -> WeekMask:
        return WeekMask(self.bits & other.bits)

    def union(self, other: WeekMask) -> WeekMask:
        return WeekMask(self.bits | other.bits)

    def __bool__(self) -> bool:
        return bool(self.bits)

    def __len__(self) -> int:
        return self.bits.bit_count()

    def __iter__(self) -> Iterator[int]:
        remaining = self.bits
        while remaining:
            least_significant = remaining & -remaining
            yield least_significant.bit_length()
            remaining ^= least_significant


def _validate_week(week: int) -> None:
    if isinstance(week, bool) or not isinstance(week, int):
        raise TypeError("academic week must be an int")
    if not 1 <= week <= MAX_ACADEMIC_WEEK:
        raise ValueError(f"academic week must be between 1 and {MAX_ACADEMIC_WEEK}")
