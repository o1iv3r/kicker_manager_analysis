"""Tests pinning the scoring rules to the published tables in ``doc/rules.md``."""

import pytest

from kicker_manager_analysis.scoring import (
    GOAL_POINTS,
    Position,
    points_for_goals,
    points_for_grade,
)

GRADE_TABLE = [
    (1.0, 10.0),
    (1.5, 8.0),
    (2.0, 6.0),
    (2.5, 4.0),
    (3.0, 2.0),
    (3.5, 0.0),
    (4.0, -2.0),
    (4.5, -4.0),
    (5.0, -6.0),
    (5.5, -8.0),
    (6.0, -10.0),
]


@pytest.mark.parametrize(("grade", "expected"), GRADE_TABLE)
def test_points_for_grade_matches_published_table(grade: float, expected: float) -> None:
    """Every row of the published grade table is reproduced exactly."""
    assert points_for_grade(grade) == pytest.approx(expected)


@pytest.mark.parametrize("grade", [0.9, 6.1, -1.0, 10.0])
def test_points_for_grade_rejects_grades_outside_the_scale(grade: float) -> None:
    """Grades outside 1.0-6.0 are not on the kicker scale and are rejected."""
    with pytest.raises(ValueError, match="grade must be between"):
        points_for_grade(grade)


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (Position.GOALKEEPER, 6),
        (Position.DEFENDER, 5),
        (Position.MIDFIELDER, 4),
        (Position.FORWARD, 3),
    ],
)
def test_points_for_goals_scales_with_position(position: Position, expected: int) -> None:
    """A goal is worth more the further back the scorer is deployed."""
    assert points_for_goals(position, 1) == expected
    assert points_for_goals(position, 3) == 3 * expected


def test_points_for_goals_is_zero_without_goals() -> None:
    """Scoring no goals contributes nothing."""
    assert points_for_goals(Position.FORWARD, 0) == 0


def test_points_for_goals_rejects_negative_counts() -> None:
    """A negative goal count is a data error, not a deduction."""
    with pytest.raises(ValueError, match="goals must not be negative"):
        points_for_goals(Position.FORWARD, -1)


def test_goal_points_cover_every_position() -> None:
    """No position may be missing from the goal table, or the optimizer would KeyError."""
    assert set(GOAL_POINTS) == set(Position)
