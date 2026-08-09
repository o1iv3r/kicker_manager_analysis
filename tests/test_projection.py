"""Tests for the panel-fitted market curve, the goalkeeper model and the measured blend."""

import polars as pl
import pytest

from conftest import REPO_ROOT
from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.data import POSITION_DTYPE, load_latest_players, load_panel
from kicker_manager_analysis.projection import (
    FIRST_CHOICE_APPEARANCES,
    GoalkeeperModel,
    MarketCurve,
    Registration,
    ResidualPersistence,
    estimate_residual_persistence,
    fit_and_project,
    fit_goalkeeper_model,
    fit_market_curve,
    fit_model,
    latest_residuals,
    project,
    season_residuals,
    with_keeper_rank,
)
from kicker_manager_analysis.scoring import Position

REGULAR = 20
"""Appearances given to a test player by default: a first-choice season."""


def panel_frame(rows: list[tuple[int, str, str, str, int, int]]) -> pl.DataFrame:
    """Build a panel from ``(season, player_id, club, position, value, points)`` tuples.

    Args:
        rows: One tuple per player-season.

    Returns:
        A frame with the columns the projection reads, every player a regular.
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
            "starts": [REGULAR] * len(rows),
            "sub_appearances": [0] * len(rows),
            "appearances": [REGULAR] * len(rows),
        },
        schema_overrides={"position": POSITION_DTYPE, "season": pl.Int32},
    )


def fitted(panel: pl.DataFrame) -> tuple[MarketCurve, GoalkeeperModel]:
    """Fit the curve and the goalkeeper model on one panel.

    Args:
        panel: A panel frame.

    Returns:
        The curve and the goalkeeper model.
    """
    return fit_market_curve(panel, Settings()), fit_goalkeeper_model(panel)


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
    curve, goalkeepers = fitted(panel)
    residuals = season_residuals(panel, curve, goalkeepers)
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
    panel = panel_frame(rows)
    persistence = estimate_residual_persistence(panel, *fitted(panel))
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
    persistence = estimate_residual_persistence(panel, *fitted(panel))
    for position in Position:
        assert persistence.weights[position] == 0.0
        assert persistence.correlations[position] < 0.0


def test_absent_players_stay_on_the_curve() -> None:
    """A player with no previous season is a cold start, not a zero."""
    panel = linear_panel(4.0e-5, dict.fromkeys(Position, 20.0))
    curve, goalkeepers = fitted(panel)
    persistence = estimate_residual_persistence(panel, curve, goalkeepers)
    pool = (
        panel.filter(pl.col("season") == 2025)
        .drop("season")
        .with_columns(pl.lit("pl-new").alias("player_id"))
    )
    projected = project(
        pool,
        Settings(),
        curve,
        goalkeepers,
        persistence,
        latest_residuals(panel, curve, goalkeepers),
    )

    assert (
        projected.get_column("registration").to_list() == [Registration.ABSENT.value] * pool.height
    )
    assert projected.get_column("residual").abs().to_numpy().max() == 0.0
    assert projected.get_column("projected_points").to_list() == pytest.approx(
        projected.get_column("market_points").to_list()
    )


def test_settings_override_replaces_the_measured_weight() -> None:
    """The measured weight is the default; an explicit setting must win."""
    panel = linear_panel(4.0e-5, dict.fromkeys(Position, 20.0))
    curve, goalkeepers = fitted(panel)
    persistence = ResidualPersistence(
        weights=dict.fromkeys(Position, 0.0),
        correlations=dict.fromkeys(Position, 0.0),
        pair_counts=dict.fromkeys(Position, 10),
    )
    pool = panel.filter(pl.col("season") == 2025).drop("season")
    residuals = pl.DataFrame(
        {
            "player_id": pool.get_column("player_id"),
            "residual": [100.0] * pool.height,
            "previous_appearances": [REGULAR] * pool.height,
        }
    )

    ignored = project(pool, Settings(), curve, goalkeepers, persistence, residuals)
    honoured = project(
        pool, Settings(residual_weight=1.0), curve, goalkeepers, persistence, residuals
    )
    assert honoured.get_column("projected_points").to_list() == pytest.approx(
        [value + 100.0 for value in ignored.get_column("projected_points").to_list()]
    )


def test_projected_points_are_never_negative() -> None:
    """Below break-even the straight line runs negative, but a player who never plays scores 0."""
    panel = linear_panel(4.0e-5, dict.fromkeys(Position, -100.0))
    curve, goalkeepers = fitted(panel)
    persistence = estimate_residual_persistence(panel, curve, goalkeepers)
    pool = panel.filter(pl.col("season") == 2025).drop("season")
    projected = project(
        pool,
        Settings(),
        curve,
        goalkeepers,
        persistence,
        latest_residuals(panel, curve, goalkeepers),
    )

    assert projected.get_column("market_points").to_numpy().min() < 0
    assert projected.get_column("projected_points").to_numpy().min() >= 0.0


def keeper_panel() -> pl.DataFrame:
    """Build a panel where each club's dearer keeper plays and the cheaper one does not.

    Returns:
        A panel carrying four clubs' worth of that step, plus outfield players so the curve fits.
    """
    rows: list[tuple[int, str, str, str, int, int]] = []
    for season in (2024, 2025):
        for club in range(4):
            rows.append((season, f"gk-{club}-1", f"Club {club}", "GOALKEEPER", 3_000_000, 180))
            rows.append((season, f"gk-{club}-2", f"Club {club}", "GOALKEEPER", 500_000, 10))
            for position in (Position.DEFENDER, Position.MIDFIELDER, Position.FORWARD):
                rows.append(
                    (
                        season,
                        f"{position.value}-{club}",
                        f"Club {club}",
                        position.value,
                        2_000_000,
                        90,
                    )
                )
    frame = panel_frame(rows)
    return frame.with_columns(
        pl.when(pl.col("points") < 50)
        .then(pl.lit(0))
        .otherwise(pl.lit(REGULAR))
        .alias("appearances")
    )


def test_keeper_rank_is_within_club_and_position() -> None:
    """Ranking a keeper against his club's outfield players would destroy the signal."""
    ranked = with_keeper_rank(keeper_panel())
    keepers = ranked.filter(pl.col("position") == Position.GOALKEEPER.value)
    assert (
        keepers.filter(pl.col("market_value") == 3_000_000).get_column("keeper_rank").to_list()
        == [1] * 8
    )
    assert (
        keepers.filter(pl.col("market_value") == 500_000).get_column("keeper_rank").to_list()
        == [2] * 8
    )
    outfield = ranked.filter(pl.col("position") != Position.GOALKEEPER.value)
    assert outfield.get_column("keeper_rank").null_count() == outfield.height


def test_goalkeeper_model_recovers_a_planted_step() -> None:
    """The number one plays and his deputy does not, whatever the deputy costs."""
    model = fit_goalkeeper_model(keeper_panel())
    assert model.first_choice_probability[1] == pytest.approx(1.0)
    assert model.first_choice_probability[2] == pytest.approx(0.0)
    assert model.deputy_points == pytest.approx(10.0)
    assert model.sample_size == 16


def test_goalkeeper_model_needs_a_first_choice_keeper() -> None:
    """With nobody above the appearance cut the first-choice branch is unidentified."""
    panel = keeper_panel().with_columns(pl.lit(0).alias("appearances"))
    with pytest.raises(ValueError, match="no first-choice keeper"):
        fit_goalkeeper_model(panel)


def test_goalkeepers_take_the_step_model_not_the_curve() -> None:
    """A cheap deputy must not inherit the curve's value, which is the Phase 3 anomaly."""
    panel = keeper_panel()
    model = fit_model(panel, Settings())
    pool = panel.filter(pl.col("season") == 2025).drop("season")
    projected = project(
        pool,
        Settings(),
        model.curve,
        model.goalkeepers,
        model.persistence,
        latest_residuals(panel, model.curve, model.goalkeepers),
    )
    keepers = projected.filter(pl.col("position") == Position.GOALKEEPER.value)
    number_ones = keepers.filter(pl.col("keeper_rank") == 1)
    deputies = keepers.filter(pl.col("keeper_rank") == 2)
    assert number_ones.get_column("market_points").to_numpy().min() > 150.0
    assert deputies.get_column("market_points").to_numpy().max() < 50.0
    assert deputies.get_column("points_per_million").to_numpy().max() < (
        number_ones.get_column("points_per_million").to_numpy().min()
    )


def test_real_panel_number_one_keepers_dominate() -> None:
    """The step the model rests on has to be present in the committed data.

    The plan's criterion: the dearest keeper at a club is also the one who plays. If this drops
    toward chance the step-function model is wrong and should be dropped rather than tuned.
    """
    panel = with_keeper_rank(load_panel(REPO_ROOT / "data")).filter(
        pl.col("position") == Position.GOALKEEPER.value
    )
    per_club = panel.group_by("season", "club").agg(
        most_appearances=pl.col("appearances").max(),
        rank_one_appearances=pl.col("appearances").get(pl.col("market_value").arg_max()),
    )
    agreed = per_club.filter(pl.col("rank_one_appearances") == pl.col("most_appearances")).height
    assert agreed / per_club.height > 0.8

    by_rank = panel.group_by("keeper_rank").agg(pl.col("appearances").mean()).sort("keeper_rank")
    appearances = by_rank.get_column("appearances").to_list()
    assert appearances[0] > 25.0
    assert appearances[1] < FIRST_CHOICE_APPEARANCES / 2


def test_real_pool_splits_three_ways_on_registration() -> None:
    """Every player must land in exactly one of the three previous-season states."""
    settings = Settings(data_dir=REPO_ROOT / "data")
    panel = load_panel(settings.data_dir)
    projected, _ = fit_and_project(panel, load_latest_players(settings), settings)

    counts = dict(projected.group_by("registration").len().iter_rows())
    assert set(counts) == {status.value for status in Registration}
    assert sum(counts.values()) == projected.height
    # An absent player has no observation to blend, so he sits exactly on the model's prior.
    absent = projected.filter(pl.col("registration") == Registration.ABSENT.value)
    assert absent.get_column("residual").abs().to_numpy().max() == 0.0


def test_real_panel_fit_is_sane() -> None:
    """The curve fitted on the committed exports must stay economically sensible.

    Bounds are loose on purpose — they catch a broken fit, not a shifted season.
    """
    settings = Settings(data_dir=REPO_ROOT / "data")
    panel = load_panel(settings.data_dir)
    projected, model = fit_and_project(panel, load_latest_players(settings), settings)

    assert 25.0 < model.curve.points_per_million < 60.0
    assert model.curve.sample_size == 1380
    assert projected.height == 549
    assert projected.get_column("projected_points").to_numpy().min() >= 0.0
    assert model.persistence.weights[Position.GOALKEEPER] > 0.2
    assert all(
        model.persistence.weights[position] < 0.1
        for position in Position
        if position is not Position.GOALKEEPER
    )
