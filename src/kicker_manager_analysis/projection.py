"""Baseline projection of expected season points, using only the player export.

The market value is not an input the kicker editorial team derived from last season's points; it
is their forecast of the coming one. That makes it the natural prior, and last season's points a
noisy observation to be blended into it. Every projection here is therefore

    projected = market curve  +  residual_weight x (observed - market curve)

which degenerates to the pure prior for the 40% of the pool with no Bundesliga history, without
ever treating their missing season as a zero.

Refining the observation term needs appearance counts, which the export does not carry (see
``doc/plan.md``); until they are ingested, ``Settings.residual_weight`` governs the blend.
"""

from typing import Final

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict, Field
from sklearn.linear_model import LinearRegression

from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.scoring import Position

EUROS_PER_MILLION: Final = 1_000_000
"""Market values are fitted in millions, not euros.

Least squares against a column of the raw values leaves the design matrix with a condition number
of 8.8e6 against the 0/1 position dummies, and the rank cutoff then discards the dummies and
silently returns zero for every intercept. In millions the condition number is 9.
"""

HAS_HISTORY: Final = (pl.col("points") != 0) | (pl.col("grade_average") != 0)
"""A player appeared in the league last season.

Any appearance is worth at least the two-point substitute bonus and any appearance long enough to
be graded leaves a non-zero average, so a player who is zero on both counts never featured. The
distinction matters because such a zero is missing data, not a measurement of a bad season.
"""


class MarketCurve(BaseModel):
    """The points-per-euro curve implied by the editorial market values.

    One shared slope with a per-position intercept: cross-validation gives per-position slopes no
    advantage (R^2 0.727 against 0.726), and the 24 goalkeepers with history are too few to
    support one.

    Attributes:
        slope: Expected season points per euro of market value.
        intercepts: Position-specific offset, in points.
        residual_sd: Spread of the fit residuals per position, for later uncertainty work.
        sample_size: Number of players the curve was fitted on.
        excluded_clubs: Clubs held out of the fit as new to the league.
    """

    model_config = ConfigDict(frozen=True)

    slope: float
    intercepts: dict[Position, float]
    residual_sd: dict[Position, float]
    sample_size: int = Field(gt=0)
    excluded_clubs: tuple[str, ...]

    @property
    def points_per_million(self) -> float:
        """The slope expressed per million euros, which is how market values read."""
        return self.slope * EUROS_PER_MILLION

    def break_even(self, position: Position) -> float:
        """Return the market value at which a player of this position first returns points.

        Args:
            position: Tactical position.

        Returns:
            Market value in euros where the curve crosses zero.
        """
        return -self.intercepts[position] / self.slope

    def expected_points(self) -> pl.Expr:
        """Return the expression evaluating the curve over a player frame.

        Returns:
            A Polars expression in ``position`` and ``market_value``, unclipped so that callers
            can inspect where the curve falls below zero.
        """
        intercept = pl.col("position").replace_strict(
            {position.value: value for position, value in self.intercepts.items()},
            return_dtype=pl.Float64,
        )
        return intercept + self.slope * pl.col("market_value")


def new_clubs(players: pl.DataFrame, threshold: float) -> tuple[str, ...]:
    """Identify clubs that were not in the league last season.

    Promoted clubs are recognised by their squads being almost entirely without history, which
    separates them cleanly from established clubs — in the 2026-08-09 export the three promoted
    sides sit at 3-6% while every other club is above 57%. Deriving this from the data rather
    than naming the clubs keeps the model correct for later seasons.

    Args:
        players: Canonical player frame.
        threshold: Share of a squad with history below which the club counts as new.

    Returns:
        The names of the clubs, sorted.
    """
    shares = players.group_by("club").agg(HAS_HISTORY.mean().alias("share"))
    return tuple(sorted(shares.filter(pl.col("share") < threshold).get_column("club")))


def fit_market_curve(players: pl.DataFrame, settings: Settings) -> MarketCurve:
    """Fit the market curve by least squares on players who were in the league last season.

    Players at promoted clubs are excluded because their zero is missing data. Players at
    established clubs are kept **whether or not they featured**: there, a zero means the player
    was available and did not play, which is exactly the outcome the curve has to predict.
    Dropping those rows biases the curve badly at the cheap end — 37 of the 41 goalkeepers priced
    at 500k never played, and fitting on the four who did makes them look like the best value in
    the pool.

    Args:
        players: Canonical player frame.
        settings: Supplies the threshold separating new clubs from established ones.

    Returns:
        The fitted curve.

    Raises:
        ValueError: If any position is unrepresented in the fit sample.
    """
    excluded = new_clubs(players, settings.new_club_threshold)
    sample = players.filter(~pl.col("club").is_in(excluded))

    missing = sorted(set(Position) - set(sample.get_column("position").unique()))
    if missing:
        raise ValueError(f"cannot fit the market curve, no players at: {missing}")

    positions = sample.get_column("position").to_numpy()
    dummies = np.column_stack([(positions == position.value) for position in Position]).astype(
        float
    )
    millions = sample.get_column("market_value").to_numpy().astype(float) / EUROS_PER_MILLION
    points = sample.get_column("points").to_numpy().astype(float)

    design = np.column_stack([millions, dummies])
    fitted = LinearRegression(fit_intercept=False).fit(design, points)
    slope = float(fitted.coef_[0]) / EUROS_PER_MILLION
    intercepts = {
        position: float(value) for position, value in zip(Position, fitted.coef_[1:], strict=True)
    }

    residuals = points - fitted.predict(design)
    residual_sd = {
        position: float(np.std(residuals[positions == position.value], ddof=1))
        for position in Position
    }

    return MarketCurve(
        slope=slope,
        intercepts=intercepts,
        residual_sd=residual_sd,
        sample_size=sample.height,
        excluded_clubs=excluded,
    )


def project(players: pl.DataFrame, settings: Settings, curve: MarketCurve) -> pl.DataFrame:
    """Attach expected season points to every player in the pool.

    Args:
        players: Canonical player frame.
        settings: Supplies ``residual_weight``.
        curve: The fitted market curve.

    Returns:
        The frame with ``has_history``, ``market_points``, ``residual``, ``projected_points`` and
        ``points_per_million`` appended. ``residual`` is zero wherever there is no history, so
        those players sit exactly on the curve. ``projected_points`` is floored at zero: below the
        break-even value the straight line runs negative, but a player who never features scores
        nothing rather than losing points.
    """
    return (
        players.with_columns(
            HAS_HISTORY.alias("has_history"),
            curve.expected_points().alias("market_points"),
        )
        .with_columns(
            pl.when(pl.col("has_history"))
            .then(pl.col("points") - pl.col("market_points"))
            .otherwise(0.0)
            .alias("residual")
        )
        .with_columns(
            pl.max_horizontal(
                pl.col("market_points") + settings.residual_weight * pl.col("residual"),
                pl.lit(0.0),
            ).alias("projected_points")
        )
        .with_columns(
            (pl.col("projected_points") / pl.col("market_value") * EUROS_PER_MILLION).alias(
                "points_per_million"
            )
        )
    )


def project_latest(players: pl.DataFrame, settings: Settings) -> tuple[pl.DataFrame, MarketCurve]:
    """Fit the curve on a pool and project it in one step.

    Args:
        players: Canonical player frame.
        settings: Supplies the fit and blend parameters.

    Returns:
        The projected frame and the curve it was built from.
    """
    curve = fit_market_curve(players, settings)
    return project(players, settings, curve), curve
