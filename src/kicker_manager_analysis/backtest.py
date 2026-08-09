"""Out-of-sample validation of the projection against the baselines it has to beat.

The panel makes the honest test possible: hold one season out, fit on the rest, and predict the
held-out season from information that existed before it started. A model is only worth its
complexity if it beats both of the trivial alternatives —

- **market value alone**, since the price is already the editorial forecast, and
- **last season's points carried forward**, the naive persistence assumption.

Cross-fitting matters more here than usual. Only two seasons can be predicted at all, because a
lagged baseline needs a season before it, so a single train/test split is one draw from a very
small urn. Reporting each held-out season separately keeps that visible rather than averaging it
away.
"""

from typing import Final

import numpy as np
import polars as pl

from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.projection import (
    baseline_expression,
    fit_model,
    with_keeper_rank,
)

MINIMUM_TRAINING_SEASONS: Final = 1
"""Seasons that must remain after holding one out, or there is nothing to fit on."""


def _score(actual: np.ndarray, predicted: np.ndarray) -> tuple[float, float]:
    """Return RMSE and R^2 of a prediction.

    Args:
        actual: Realised season points.
        predicted: Projected season points.

    Returns:
        Root mean squared error, and the share of variance explained. R^2 is computed against the
        held-out season's own mean, so a negative value means the model is worse than having
        guessed that mean — which is the comparison that matters for a season nobody has seen.
    """
    error = actual - predicted
    rmse = float(np.sqrt(np.mean(error**2)))
    total = float(np.sum((actual - actual.mean()) ** 2))
    r_squared = 1.0 - float(np.sum(error**2)) / total if total else 0.0
    return rmse, r_squared


def backtest(panel: pl.DataFrame, settings: Settings) -> pl.DataFrame:
    """Predict each held-out season from the others and score it against the baselines.

    Every season that has a predecessor in the panel is held out in turn. The model is refitted
    from scratch on the remaining seasons, so nothing about the held-out season — not the curve,
    not the goalkeeper step, not the blend weights — is informed by its outcome.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        settings: Passed to the fit.

    Returns:
        One row per held-out season and model, with columns ``season``, ``model``, ``players``,
        ``rmse`` and ``r_squared``. Models are ``projection``, ``market_value`` and
        ``previous_points``.

    Raises:
        ValueError: If the panel holds too few seasons to hold one out and still fit.
    """
    seasons = sorted(panel.get_column("season").unique().to_list())
    if len(seasons) <= MINIMUM_TRAINING_SEASONS:
        raise ValueError(f"cannot backtest {len(seasons)} season(s); at least two are needed")

    rows = []
    for held_out in seasons[1:]:
        train = panel.filter(pl.col("season") != held_out)
        model = fit_model(train, settings)

        previous = panel.filter(pl.col("season") == held_out - 1).select(
            "player_id", pl.col("points").alias("previous_points")
        )
        test = (
            with_keeper_rank(panel.filter(pl.col("season") == held_out))
            .join(previous, on="player_id", how="inner")
            .with_columns(
                baseline_expression(model.curve, model.goalkeepers).alias("projection"),
                model.curve.expected_points().alias("market_value"),
            )
        )
        if test.is_empty():
            continue

        actual = test.get_column("points").to_numpy().astype(float)
        for name in ("projection", "market_value", "previous_points"):
            rmse, r_squared = _score(actual, test.get_column(name).to_numpy().astype(float))
            rows.append(
                {
                    "season": held_out,
                    "model": name,
                    "players": test.height,
                    "rmse": rmse,
                    "r_squared": r_squared,
                }
            )

    return pl.DataFrame(rows)


def backtest_summary(results: pl.DataFrame) -> pl.DataFrame:
    """Average a backtest over its held-out seasons.

    Args:
        results: Output of :func:`backtest`.

    Returns:
        One row per model, ordered best first by mean RMSE.
    """
    return (
        results.group_by("model")
        .agg(
            seasons=pl.len(),
            rmse=pl.col("rmse").mean(),
            r_squared=pl.col("r_squared").mean(),
        )
        .sort("rmse")
    )
