from app.domain import (
    Meeting,
    SectionOption,
    TeachingSection,
    WeekMask,
    WeekParity,
    option_conflicts,
    options_overlap,
)


def _option(
    option_id: str,
    course_id: str,
    *,
    weekday: int,
    periods: tuple[int, int],
    weeks: WeekMask,
) -> SectionOption:
    section = TeachingSection(
        id=f"section-{option_id}",
        course_id=course_id,
        section_code=option_id,
        instructors=("教师",),
        meetings=(
            Meeting(
                weeks=weeks,
                weekday=weekday,
                start_period=periods[0],
                end_period=periods[1],
            ),
        ),
    )
    return SectionOption(
        id=option_id,
        course_id=course_id,
        sections=(section,),
    )


def test_discrete_weeks_round_trip_and_overlap() -> None:
    weeks = WeekMask.from_weeks([1, 3, 7, 10])

    assert weeks.weeks == (1, 3, 7, 10)
    assert weeks.contains(7)
    assert not weeks.contains(8)
    assert weeks.intersection(WeekMask.from_weeks([2, 3, 8])).weeks == (3,)


def test_odd_and_even_week_ranges_do_not_conflict() -> None:
    odd = WeekMask.from_range(5, 19, WeekParity.ODD)
    even = WeekMask.from_range(5, 19, WeekParity.EVEN)
    left = _option("odd", "course-a", weekday=3, periods=(3, 4), weeks=odd)
    right = _option("even", "course-b", weekday=3, periods=(3, 4), weeks=even)

    assert odd.weeks == (5, 7, 9, 11, 13, 15, 17, 19)
    assert even.weeks == (6, 8, 10, 12, 14, 16, 18)
    assert not options_overlap(left, right)


def test_same_slot_in_disjoint_week_segments_is_not_a_conflict() -> None:
    early = _option(
        "early",
        "course-a",
        weekday=5,
        periods=(5, 6),
        weeks=WeekMask.from_weeks([1, 2, 3, 6, 7, 8, 10]),
    )
    late = _option(
        "late",
        "course-b",
        weekday=5,
        periods=(5, 6),
        weeks=WeekMask.from_range(12, 17),
    )

    assert not options_overlap(early, late)


def test_sunday_makeup_class_conflicts_only_in_its_actual_week() -> None:
    sunday_makeup = _option(
        "makeup",
        "course-a",
        weekday=7,
        periods=(3, 4),
        weeks=WeekMask.from_weeks([3]),
    )
    week_three = _option(
        "week-3",
        "course-b",
        weekday=7,
        periods=(4, 5),
        weeks=WeekMask.from_weeks([3]),
    )
    week_four = _option(
        "week-4",
        "course-c",
        weekday=7,
        periods=(3, 4),
        weeks=WeekMask.from_weeks([4]),
    )

    conflicts = option_conflicts(sunday_makeup, week_three)
    assert len(conflicts) == 1
    assert conflicts[0].overlapping_weeks == (3,)
    assert not options_overlap(sunday_makeup, week_four)
