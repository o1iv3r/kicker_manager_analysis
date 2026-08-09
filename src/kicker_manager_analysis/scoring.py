"""The kicker-Managerspiel Classic scoring rules.

Mirrors the tables in ``doc/rules.md``. Nothing here depends on the player pool, so these
functions double as the reference implementation used to sanity-check the ``Punkte`` column
of the kicker export.
"""

from enum import StrEnum
from typing import Final


class Position(StrEnum):
    """Tactical position assigned by the kicker editorial team before the season.

    The values match the ``Position`` column of the kicker player-data export. A player's
    position is fixed for the whole season, even if he plays elsewhere on the pitch.
    """

    GOALKEEPER = "GOALKEEPER"
    DEFENDER = "DEFENDER"
    MIDFIELDER = "MIDFIELDER"
    FORWARD = "FORWARD"


BEST_GRADE: Final = 1.0
WORST_GRADE: Final = 6.0
NEUTRAL_GRADE: Final = 3.5
POINTS_PER_GRADE: Final = 4.0

STARTING_LINEUP_POINTS: Final = 4
SUBSTITUTE_POINTS: Final = 2
ASSIST_POINTS: Final = 2
CLEAN_SHEET_POINTS: Final = 2
PLAYER_OF_THE_MATCH_POINTS: Final = 3
SECOND_YELLOW_CARD_POINTS: Final = -3
RED_CARD_POINTS: Final = -6

GOAL_POINTS: Final[dict[Position, int]] = {
    Position.GOALKEEPER: 6,
    Position.DEFENDER: 5,
    Position.MIDFIELDER: 4,
    Position.FORWARD: 3,
}


def points_for_grade(grade: float) -> float:
    """Return the points awarded for a kicker grade.

    The published table is linear: a grade of 3.5 is worth nothing and every half-grade
    either side is worth two points, so 1.0 yields +10 and 6.0 yields -10.

    Args:
        grade: kicker grade, between 1.0 (best) and 6.0 (worst).

    Returns:
        The points contributed by that grade.

    Raises:
        ValueError: If the grade lies outside the 1.0-6.0 range.
    """
    if not BEST_GRADE <= grade <= WORST_GRADE:
        raise ValueError(f"grade must be between {BEST_GRADE} and {WORST_GRADE}, got {grade}")
    return (NEUTRAL_GRADE - grade) * POINTS_PER_GRADE


def points_for_goals(position: Position, goals: int) -> int:
    """Return the points awarded for goals scored, which depend on the scorer's position.

    Args:
        position: The scorer's tactical position.
        goals: Number of goals scored.

    Returns:
        The points contributed by those goals.

    Raises:
        ValueError: If ``goals`` is negative.
    """
    if goals < 0:
        raise ValueError(f"goals must not be negative, got {goals}")
    return GOAL_POINTS[position] * goals
