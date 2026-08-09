"""Tests for the baseline market-curve projection."""

import polars as pl
import pytest

from conftest import REPO_ROOT
from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.data import POSITION_DTYPE, load_latest_players
from kicker_manager_analysis.projection import (
    HAS_HISTORY,
    MarketCurve,
    fit_market_curve,
    new_clubs,
    project,
    project_latest,
)
from kicker_manager_analysis.scoring import Position


def pool(rows: list[tuple[str, str, int, int, float]]) -> pl.DataFrame:
    """Build a canonical player frame from ``(club, position, value, points, grade)`` tuples.

    Args:
        rows: One tuple per player.

    Returns:
        A frame with the columns the projection reads.
    """
    return pl.DataFrame(
        {
            "player_id": [f"pl-{index:05d}" for index in range(len(rows))],
            "name": [f"Name {index}" for index in range(len(rows))],
            "club": [row[0] for row in rows],
            "position": [row[1] for row in rows],
            "market_value": [row[2] for row in rows],
            "points": [row[3] for row in rows],
            "grade_average": [row[4] for row in rows],
        },
        schema_overrides={"position": POSITION_DTYPE},
    )


def linear_pool(
    slope: float, intercepts: dict[Position, float], club: str = "Club A"
) -> pl.DataFrame:
    """Build a pool lying exactly on a known curve, so the fit has to recover it.

    Args:
        slope: Points per euro.
        intercepts: Position offsets in points.
        club: Club to assign every player to.

    Returns:
        A frame whose points are an exact linear function of market value.
    """
    rows = []
    for position, intercept in intercepts.items():
        for value in (1_000_000, 2_000_000, 3_000_000, 4_000_000):
            rows.append((club, position.value, value, round(intercept + slope * value), 3.0))
    return pool(rows)


def test_has_history_needs_points_or_a_grade() -> None:
    """Any appearance leaves points or a grade, so only a player with neither never featured."""
    players = pool(
        [
            ("Club A", "MIDFIELDER", 1_000_000, 0, 0.0),
            ("Club A", "MIDFIELDER", 1_000_000, 12, 0.0),
            ("Club A", "MIDFIELDER", 1_000_000, 0, 5.0),
            ("Club A", "MIDFIELDER", 1_000_000, 90, 2.5),
        ]
    )
    assert players.select(HAS_HISTORY).to_series().to_list() == [False, True, True, True]


def test_new_clubs_flags_squads_without_history() -> None:
    """A promoted club is recognised by the share of its squad with history, not by name."""
    players = pool(
        [
            ("Promoted", "MIDFIELDER", 1_000_000, 0, 0.0),
            ("Promoted", "DEFENDER", 1_000_000, 0, 0.0),
            ("Promoted", "FORWARD", 1_000_000, 0, 0.0),
            ("Promoted", "GOALKEEPER", 1_000_000, 0, 0.0),
            ("Promoted", "MIDFIELDER", 1_000_000, 8, 0.0),
            ("Established", "MIDFIELDER", 1_000_000, 100, 3.0),
            ("Established", "DEFENDER", 1_000_000, 0, 0.0),
        ]
    )
    assert new_clubs(players, threshold=0.25) == ("Promoted",)
    assert new_clubs(players, threshold=0.2) == ()


def test_fit_recovers_a_known_curve() -> None:
    """The fit must return the generating coefficients when the pool is exactly linear.

    This is the guard against a badly conditioned design matrix: fitting market values in euros
    against 0/1 position dummies makes the rank cutoff drop the dummies and silently report every
    intercept as zero.
    """
    slope = 5.5e-5
    intercepts = {
        Position.GOALKEEPER: -10.0,
        Position.DEFENDER: -30.0,
        Position.MIDFIELDER: -40.0,
        Position.FORWARD: -55.0,
    }
    curve = fit_market_curve(linear_pool(slope, intercepts), Settings())

    assert curve.slope == pytest.approx(slope, rel=1e-3)
    for position, expected in intercepts.items():
        assert curve.intercepts[position] == pytest.approx(expected, abs=1.0)


def test_fit_excludes_new_clubs() -> None:
    """Players whose club was not in the league must not drag the curve down."""
    established = linear_pool(5.5e-5, dict.fromkeys(Position, 0.0), club="Established")
    promoted = pool([("Promoted", position.value, 2_000_000, 0, 0.0) for position in Position])
    curve = fit_market_curve(pl.concat([established, promoted]), Settings())

    assert curve.excluded_clubs == ("Promoted",)
    assert curve.sample_size == established.height
    assert curve.slope == pytest.approx(5.5e-5, rel=1e-3)


def test_fit_rejects_a_pool_missing_a_position() -> None:
    """An unrepresented position would leave its intercept undefined rather than merely noisy."""
    players = pool([("Club A", "MIDFIELDER", 1_000_000, 100, 3.0)] * 4)
    with pytest.raises(ValueError, match="no players at"):
        fit_market_curve(players, Settings())


def test_break_even_is_where_the_curve_crosses_zero() -> None:
    """Break-even is the market value below which a player is not expected to return points."""
    curve = MarketCurve(
        slope=5.0e-5,
        intercepts=dict.fromkeys(Position, -50.0),
        residual_sd=dict.fromkeys(Position, 30.0),
        sample_size=100,
        excluded_clubs=(),
    )
    assert curve.break_even(Position.DEFENDER) == pytest.approx(1_000_000)
    assert curve.points_per_million == pytest.approx(50.0)


def test_players_without_history_sit_on_the_curve() -> None:
    """The cold-start cohort gets the market prior, never an implicit zero."""
    players = linear_pool(5.5e-5, dict.fromkeys(Position, 20.0))
    cold = pool([("Club A", "FORWARD", 2_000_000, 0, 0.0)])
    projected, _ = project_latest(pl.concat([players, cold]), Settings())
    row = projected.filter(pl.col("points") == 0).row(0, named=True)

    assert not row["has_history"]
    assert row["residual"] == 0.0
    assert row["projected_points"] == pytest.approx(row["market_points"])


@pytest.mark.parametrize("weight", [0.0, 1.0])
def test_residual_weight_spans_curve_and_observation(weight: float) -> None:
    """Weight 0 returns the curve untouched; weight 1 returns last season's points verbatim."""
    players = linear_pool(5.5e-5, dict.fromkeys(Position, 40.0))
    settings = Settings(residual_weight=weight)
    curve = fit_market_curve(players, settings)
    projected = project(players, settings, curve).filter(pl.col("market_value") >= 2_000_000)

    if weight == 0.0:
        expected = projected.get_column("market_points")
    else:
        expected = projected.get_column("points").cast(pl.Float64)
    assert projected.get_column("projected_points").to_list() == pytest.approx(expected.to_list())


def test_projected_points_are_never_negative() -> None:
    """Below break-even the straight line runs negative, but a player who never plays scores 0."""
    players = linear_pool(5.5e-5, dict.fromkeys(Position, -100.0))
    cheap = pool([("Club A", "FORWARD", 500_000, 0, 0.0)])
    projected, _ = project_latest(pl.concat([players, cheap]), Settings())

    assert projected.get_column("market_points").to_numpy().min() < 0
    assert projected.get_column("projected_points").to_numpy().min() >= 0.0


def test_real_export_curve_is_sane() -> None:
    """The fitted curve on the committed export must stay economically sensible.

    Bounds are loose on purpose — they catch a broken fit, not a shifted season.
    """
    settings = Settings(data_dir=REPO_ROOT / "data")
    projected, curve = project_latest(load_latest_players(settings), settings)

    assert 30.0 < curve.points_per_million < 80.0
    assert all(intercept < 0.0 for intercept in curve.intercepts.values())
    assert len(curve.excluded_clubs) == 3
    assert projected.height == 549
    assert projected.get_column("projected_points").to_numpy().min() >= 0.0
    cold = projected.filter(~pl.col("has_history")).get_column("residual").to_numpy()
    assert abs(cold).max() == 0.0
