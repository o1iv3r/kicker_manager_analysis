"""Tests for the held-out-season validation of the projection."""

import polars as pl
import pytest

from conftest import REPO_ROOT
from kicker_manager_analysis.backtest import backtest, backtest_summary
from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.data import load_panel
from test_projection import keeper_panel, panel_frame


def test_backtest_needs_two_seasons() -> None:
    """One season cannot be held out and trained on at the same time."""
    panel = panel_frame(
        [
            (2025, f"pl-{n}", "Club A", position, 1_000_000, 50)
            for n in range(4)
            for position in ("GOALKEEPER", "DEFENDER", "MIDFIELDER", "FORWARD")
        ]
    )
    with pytest.raises(ValueError, match="at least two"):
        backtest(panel, Settings())


def test_backtest_scores_every_model_on_every_held_out_season() -> None:
    """Each season after the first is predicted from the others, against both baselines."""
    results = backtest(keeper_panel(), Settings())
    assert results.get_column("season").unique().to_list() == [2025]
    assert set(results.get_column("model").to_list()) == {
        "projection",
        "market_value",
        "previous_points",
    }
    assert results.get_column("rmse").to_numpy().min() >= 0.0


def test_backtest_rewards_a_model_that_fits() -> None:
    """On a panel the curve describes exactly, the projection must score a perfect fit."""
    rows = []
    for season in (2024, 2025):
        for club in range(4):
            for index, position in enumerate(("GOALKEEPER", "DEFENDER", "MIDFIELDER", "FORWARD")):
                value = 1_000_000 * (club + 1)
                rows.append(
                    (season, f"pl-{index}-{club}", f"Club {club}", position, value, value // 20_000)
                )
    results = backtest(panel_frame(rows), Settings())
    projection = results.filter(pl.col("model") == "projection")
    assert projection.get_column("rmse").to_numpy().max() == pytest.approx(0.0, abs=1e-6)


def test_real_panel_projection_beats_both_baselines() -> None:
    """The headline check: the model must earn its complexity on unseen seasons.

    It must beat the price on its own and last season's points carried forward, in *every* held-out
    season rather than on average — with only two of them, an average can hide a reversal.
    """
    results = backtest(load_panel(REPO_ROOT / "data"), Settings())
    for season in results.get_column("season").unique():
        scores = dict(
            results.filter(pl.col("season") == season).select("model", "rmse").iter_rows()
        )
        assert scores["projection"] < scores["market_value"], season
        assert scores["projection"] < scores["previous_points"], season

    summary = backtest_summary(results)
    assert summary.get_column("model").to_list()[0] == "projection"
    best = summary.filter(pl.col("model") == "projection")
    assert float(best.get_column("r_squared").to_numpy()[0]) > 0.4
