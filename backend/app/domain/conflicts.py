"""Conflict primitives shared by filtering, solving and verification."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Meeting, SectionOption


@dataclass(frozen=True, slots=True)
class MeetingConflict:
    left: Meeting
    right: Meeting
    overlapping_weeks: tuple[int, ...]


def meeting_conflict(left: Meeting, right: Meeting) -> MeetingConflict | None:
    if not left.overlaps(right):
        return None
    return MeetingConflict(
        left=left,
        right=right,
        overlapping_weeks=left.weeks.intersection(right.weeks).weeks,
    )


def option_conflicts(left: SectionOption, right: SectionOption) -> tuple[MeetingConflict, ...]:
    return tuple(
        conflict
        for left_meeting in left.meetings
        for right_meeting in right.meetings
        if (conflict := meeting_conflict(left_meeting, right_meeting)) is not None
    )


def options_overlap(left: SectionOption, right: SectionOption) -> bool:
    return any(
        left_meeting.overlaps(right_meeting)
        for left_meeting in left.meetings
        for right_meeting in right.meetings
    )
