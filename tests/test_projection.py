"""Tests for the panel-fitted market curve and the measured residual blend."""

import polars as pl
import pytest

from conftest import REPO_ROOT
from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.data import POSITION_DTYPE, load_latest_players, load_panel
from kicker_manager_analysis.projection import (
    MarketCurve,
    ResidualPersistence,
    estimate_residual_persistence,
    fit_and_project,
    fit_market_curve,
    latest_residuals,
    project,
    season_residuals,
)
from kicker_manager_analysis.scoring import Position


def panel_frame(rows: list[tuple[int, str, str, str, int, int]]) -> pl.DataFrame:
    """Build a panel from ``(season, player_id, club, position, value, points)`` tuples.

    Args:
        rows: One tuple per player-season.

    Returns:
        A frame with the columns the projection reads.
    """
    return pl.DataFrame(
        {
            "player_id": [row[1] for row in rows],
            "name": [row[1] for row in rows],
            "club": [row[2] for row in rows],
            "position": [row[3] for row in rows],
            "market_value": [row[4] for row in rows],
            "points": [row[5] for row in rows],
            "grade_average": [3.0] * len(rows),
            "season": [row[0] for row in rows],
        },
        schema_overrides={"position": POSITION_DTYPE, "season": pl.Int32},
    )


def linear_panel(
    slope: float, intercepts: dict[Position, float], seasons: tuple[int, ...] = (2024, 2025)
) -> pl.DataFrame:
    """Build a panel lying exactly on a known curve, so the fit has to recover it.

    Args:
        slope: Points per euro.
        intercepts: Position offsets in points.
        seasons: Seasons to repeat the players across.

    Returns:
        A frame whose points are an exact linear function of market value.
    """
    rows = []
    for season in seasons:
        for index, (position, intercept) in enumerate(intercepts.items()):
            for step, value in enumerate((1_000_000, 2_000_000, 3_000_000, 4_000_000)):
                rows.append(
                    (
                        season,
                        f"pl-{index}-{step}",
                        "Club A",
                        position.value,
                        value,
                        round(intercept + slope * value),
                    )
                )
    return panel_frame(rows)


def test_fit_recovers_a_known_curve() -> None:
    """The fit must return the generating coefficients when the panel is exactly linear.

    This is the guard against a badly conditioned design matrix: fitting market values in euros
    against 0/1 position dummies makes the rank cutoff drop the dummies and silently report every
    intercept as zero.
    """
    slope = 4.0e-5
    intercepts = {
        Position.GOALKEEPER: 10.0,
        Position.DEFENDER: -2.0,
        Position.MIDFIELDER: -4.0,
        Position.FORWARD: -11.0,
    }
    curve = fit_market_curve(linear_panel(slope, intercepts), Settings())

    assert curve.slope == pytest.approx(slope, rel=1e-3)
    for position, expected in intercepts.items():
        assert curve.intercepts[position] == pytest.approx(expected, abs=1.0)
    assert curve.sample_size == 32


def test_season_effects_are_centred() -> None:
    """Centred offsets let an unseen season be priced off the intercepts alone."""
    curve = fit_market_curve(
        linear_panel(4.0e-5, dict.fromkeys(Position, 0.0), seasons=(2023, 2024, 2025)),
        Settings(),
    )
    assert sum(curve.season_effects.values()) == pytest.approx(0.0, abs=1e-6)


def test_fit_rejects_a_panel_missing_a_position() -> None:
    """An unrepresented position would leave its intercept undefined rather than merely noisy."""
    panel = panel_frame([(2025, "pl-1", "Club A", "MIDFIELDER", 1_000_000, 40)])
    with pytest.raises(ValueError, match="no players at"):
        fit_market_curve(panel, Settings())


def test_break_even_is_where_the_curve_crosses_zero() -> None:
    """Break-even is the market value below which a player is not expected to return points."""
    curve = MarketCurve(
        slope=5.0e-5,
        intercepts=dict.fromkeys(Position, -50.0),
        season_effects={2025: 0.0},
        residual_sd=dict.fromkeys(Position, 30.0),
        sample_size=100,
    )
    assert curve.break_even(Position.DEFENDER) == pytest.approx(1_000_000)
    assert curve.points_per_million == pytest.approx(50.0)


def test_residuals_are_zero_on_an_exactly_linear_panel() -> None:
    """Nothing departs from a curve the data was generated from."""
    panel = linear_panel(4.0e-5, dict.fromkeys(Position, 5.0))
    residuals = season_residuals(panel, fit_market_curve(panel, Settings()))
    assert residuals.get_column("residual").abs().to_numpy().max() == pytest.approx(0.0, abs=1e-6)


def test_persistence_recovers_a_planted_carry_over() -> None:
    """A residual repeated verbatim in the next season must measure as a weight of one."""
    rows = []
    for season in (2024, 2025):
        for index, position in enumerate(Position):
            for step in range(6):
                bump = 40 if step % 2 else -40
                rows.append(
                    (season, f"pl-{index}-{step}", "Club A", position.value, 2_000_000, 80 + bump)
                )
    persistence = estimate_residual_persistence(
        panel_frame(rows), fit_market_curve(panel_frame(rows), Settings())
    )
    for position in Position:
        assert persistence.weights[position] == pytest.approx(1.0, abs=1e-6)
        assert persistence.pair_counts[position] == 6


def test_persistence_is_zero_when_seasons_are_independent() -> None:
    """A residual that flips sign between seasons carries nothing forward, and clamps at zero."""
    rows = []
    for season in (2024, 2025):
        for index, position in enumerate(Position):
            for step in range(6):
                bump = 40 if (step % 2) == (season % 2) else -40
                rows.append(
                    (season, f"pl-{index}-{step}", "Club A", position.value, 2_000_000, 80 + bump)
                )
    panel = panel_frame(rows)
    persistence = estimate_residual_persistence(panel, fit_market_curve(panel, Settings()))
    for position in Position:
        assert persistence.weights[position] == 0.0
        assert persistence.correlations[position] < 0.0


def test_absent_players_stay_on_the_curve() -> None:
    """A player with no previous season is a cold start, not a zero."""
    panel = linear_panel(4.0e-5, dict.fromkeys(Position, 20.0))
    curve = fit_market_curve(panel, Settings())
    persistence = estimate_residual_persistence(panel, curve)
    pool = (
        panel.filter(pl.col("season") == 2025)
        .drop("season")
        .with_columns(pl.lit("pl-new").alias("player_id"))
    )
    projected = project(pool, Settings(), curve, persistence, latest_residuals(panel, curve))

    assert projected.get_column("residual").abs().to_numpy().max() == 0.0
    assert projected.get_column("projected_points").to_list() == pytest.approx(
        projected.get_column("market_points").to_list()
    )


def test_settings_override_replaces_the_measured_weight() -> None:
    """The measured weight is the default; an explicit setting must win."""
    panel = linear_panel(4.0e-5, dict.fromkeys(Position, 20.0))
    curve = fit_market_curve(panel, Settings())
    persistence = ResidualPersistence(
        weights=dict.fromkeys(Position, 0.0),
        correlations=dict.fromkeys(Position, 0.0),
        pair_counts=dict.fromkeys(Position, 10),
    )
    pool = panel.filter(pl.col("season") == 2025).drop("season")
    residuals = pl.DataFrame(
        {"player_id": pool.get_column("player_id"), "residual": [100.0] * pool.height}
    )

    ignored = project(pool, Settings(), curve, persistence, residuals)
    honoured = project(pool, Settings(residual_weight=1.0), curve, persistence, residuals)
    assert honoured.get_column("projected_points").to_list() == pytest.approx(
        [value + 100.0 for value in ignored.get_column("projected_points").to_list()]
    )


def test_projected_points_are_never_negative() -> None:
    """Below break-even the straight line runs negative, but a player who never plays scores 0."""
    panel = linear_panel(4.0e-5, dict.fromkeys(Position, -100.0))
    curve = fit_market_curve(panel, Settings())
    persistence = estimate_residual_persistence(panel, curve)
    pool = panel.filter(pl.col("season") == 2025).drop("season")
    projected = project(pool, Settings(), curve, persistence, latest_residuals(panel, curve))

    assert projected.get_column("market_points").to_numpy().min() < 0
    assert projected.get_column("projected_points").to_numpy().min() >= 0.0


def test_real_panel_fit_is_sane() -> None:
    """The curve fitted on the committed exports must stay economically sensible.

    Bounds are loose on purpose — they catch a broken fit, not a shifted season.
    """
    settings = Settings(data_dir=REPO_ROOT / "data")
    panel = load_panel(settings.data_dir)
    projected, curve, persistence = fit_and_project(panel, load_latest_players(settings), settings)

    assert 25.0 < curve.points_per_million < 60.0
    assert curve.sample_size == 1380
    assert projected.height == 549
    assert projected.get_column("projected_points").to_numpy().min() >= 0.0
    assert persistence.weights[Position.GOALKEEPER] > 0.2
    assert all(
        persistence.weights[position] < 0.1
        for position in Position
        if position is not Position.GOALKEEPER
    )
