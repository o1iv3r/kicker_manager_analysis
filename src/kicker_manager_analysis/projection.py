"""Expected season points per player, fitted on the multi-season panel.

The market value is the kicker editorial team's forecast of the season to come, so it is the
natural prior, and a player's own past is a noisy observation to be blended into it:

    projected = market curve  +  weight x (last season's residual)

Two things about this are settled by data rather than assumed, and both need the panel:

- **The curve must be fitted on matching seasons.** Regressing a season's points on the *next*
  season's prices is a retrospective fit — the price already knows the outcome — and it flatters
  itself badly (R^2 0.67 against 0.43, slope 53.4 against 38.8). :func:`~.data.load_panel` yields
  only exports whose points describe their own season.
- **The blend weight is measured, not chosen.** Regressing a player's residual in one season on
  his residual in the next gives the weight directly. It comes out at essentially zero for
  outfield players — once the price is known, last season carries no further information — and
  around 0.45 for goalkeepers, whose residual is mostly the persistent fact of being first choice.
"""

from itertools import pairwise
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


class MarketCurve(BaseModel):
    """The points-per-euro curve implied by the editorial market values.

    One shared slope with a per-position intercept, plus a per-season offset absorbing league-wide
    scoring drift. Season offsets are centred on zero, so the intercepts describe an average
    season and a season the panel has never seen — the one being predicted — needs no offset.

    Attributes:
        slope: Expected season points per euro of market value.
        intercepts: Position-specific offset in points, at an average season.
        season_effects: Centred per-season offset in points.
        residual_sd: Spread of the fit residuals per position, for later uncertainty work.
        sample_size: Number of player-seasons the curve was fitted on.
    """

    model_config = ConfigDict(frozen=True)

    slope: float
    intercepts: dict[Position, float]
    season_effects: dict[int, float]
    residual_sd: dict[Position, float]
    sample_size: int = Field(gt=0)

    @property
    def points_per_million(self) -> float:
        """The slope expressed per million euros, which is how market values read."""
        return self.slope * EUROS_PER_MILLION

    def break_even(self, position: Position) -> float:
        """Return the market value at which a player of this position first returns points.

        Args:
            position: Tactical position.

        Returns:
            Market value in euros where the curve crosses zero. Negative when the intercept is
            positive, meaning players of that position are expected to return points at any price.
        """
        return -self.intercepts[position] / self.slope

    def expected_points(self, season: int | None = None) -> pl.Expr:
        """Return the expression evaluating the curve over a player frame.

        Args:
            season: Season to price for. Omit — or pass a season outside the fitted panel — to
                use an average season, which is what prediction requires.

        Returns:
            A Polars expression in ``position`` and ``market_value``, unclipped so that callers
            can inspect where the curve falls below zero.
        """
        intercept = pl.col("position").replace_strict(
            {position.value: value for position, value in self.intercepts.items()},
            return_dtype=pl.Float64,
        )
        offset = self.season_effects.get(season, 0.0) if season is not None else 0.0
        return intercept + offset + self.slope * pl.col("market_value")


class ResidualPersistence(BaseModel):
    """How much of a player's over- or under-performance carries into the next season.

    Attributes:
        weights: Per-position blend weight, the slope of next season's residual on this one's,
            clamped to [0, 1].
        correlations: The underlying year-over-year correlation, unclamped, so a negative
            (mean-reverting) estimate stays visible after clamping.
        pair_counts: Number of player-season transitions each estimate rests on.
    """

    model_config = ConfigDict(frozen=True)

    weights: dict[Position, float]
    correlations: dict[Position, float]
    pair_counts: dict[Position, int]

    def weight_expression(self) -> pl.Expr:
        """Return the expression mapping each player's position to its blend weight.

        Returns:
            A Polars expression in ``position``.
        """
        return pl.col("position").replace_strict(
            {position.value: value for position, value in self.weights.items()},
            return_dtype=pl.Float64,
        )


def fit_market_curve(panel: pl.DataFrame, settings: Settings) -> MarketCurve:
    """Fit the market curve by least squares over every season in the panel.

    Every player in a season's export is a valid observation: the points describe the season his
    price was set for, so a promoted club's players count exactly like anyone else's, and a zero
    means he was available and did not play. This is why the panel needs no cold-start exclusion
    where the single-season fit did — that exclusion was an artefact of pairing one season's
    prices with another season's points.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`, carrying a ``season`` column.
        settings: Present for symmetry with the rest of the pipeline; unused for now.

    Returns:
        The fitted curve.

    Raises:
        ValueError: If any position is unrepresented in the panel.
    """
    del settings

    missing = sorted(set(Position) - set(panel.get_column("position").unique()))
    if missing:
        raise ValueError(f"cannot fit the market curve, no players at: {missing}")

    positions = panel.get_column("position").to_numpy()
    seasons = panel.get_column("season").to_numpy()
    ordered_seasons = sorted({int(season) for season in seasons})

    position_dummies = np.column_stack(
        [(positions == position.value) for position in Position]
    ).astype(float)
    season_dummies = np.column_stack(
        [(seasons == season) for season in ordered_seasons[1:]]
    ).astype(float)

    millions = panel.get_column("market_value").to_numpy().astype(float) / EUROS_PER_MILLION
    points = panel.get_column("points").to_numpy().astype(float)
    design = np.column_stack([millions, position_dummies, season_dummies])
    fitted = LinearRegression(fit_intercept=False).fit(design, points)

    raw_season = dict(zip(ordered_seasons, [0.0, *fitted.coef_[1 + len(Position) :]], strict=True))
    mean_season = float(np.mean(list(raw_season.values())))

    residuals = points - fitted.predict(design)
    return MarketCurve(
        slope=float(fitted.coef_[0]) / EUROS_PER_MILLION,
        intercepts={
            position: float(value) + mean_season
            for position, value in zip(Position, fitted.coef_[1 : 1 + len(Position)], strict=True)
        },
        season_effects={
            season: float(effect) - mean_season for season, effect in raw_season.items()
        },
        residual_sd={
            position: float(np.std(residuals[positions == position.value], ddof=1))
            for position in Position
        },
        sample_size=panel.height,
    )


def season_residuals(panel: pl.DataFrame, curve: MarketCurve) -> pl.DataFrame:
    """Return each player-season's departure from the curve.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        curve: The fitted curve.

    Returns:
        ``player_id``, ``season``, ``position`` and ``residual``.
    """
    per_season = []
    for season in sorted(panel.get_column("season").unique().to_list()):
        per_season.append(
            panel.filter(pl.col("season") == season)
            .with_columns((pl.col("points") - curve.expected_points(int(season))).alias("residual"))
            .select("player_id", "season", "position", "residual")
        )
    return pl.concat(per_season)


def estimate_residual_persistence(panel: pl.DataFrame, curve: MarketCurve) -> ResidualPersistence:
    """Measure how far a player's residual predicts his residual the following season.

    This is the quantity a single export cannot identify. Weights are clamped to [0, 1]: the
    outfield estimates land slightly negative, which would mean over-performance predicts
    under-performance, and at these correlations that is noise rather than mean reversion worth
    acting on. The unclamped correlation is kept so the clamping stays visible.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        curve: The fitted curve.

    Returns:
        The estimated persistence per position.
    """
    residuals = season_residuals(panel, curve)
    seasons = sorted(residuals.get_column("season").unique().to_list())
    transitions = pl.concat(
        residuals.filter(pl.col("season") == earlier)
        .select("player_id", "position", "residual")
        .join(
            residuals.filter(pl.col("season") == later).select(
                "player_id", pl.col("residual").alias("next_residual")
            ),
            on="player_id",
        )
        for earlier, later in pairwise(seasons)
    )

    weights: dict[Position, float] = {}
    correlations: dict[Position, float] = {}
    counts: dict[Position, int] = {}
    for position in Position:
        pairs = transitions.filter(pl.col("position") == position.value)
        counts[position] = pairs.height
        if pairs.height < 2:
            weights[position] = 0.0
            correlations[position] = 0.0
            continue
        current = pairs.get_column("residual").to_numpy()
        following = pairs.get_column("next_residual").to_numpy()
        slope = float(LinearRegression().fit(current[:, None], following).coef_[0])
        weights[position] = min(max(slope, 0.0), 1.0)
        correlations[position] = float(np.corrcoef(current, following)[0, 1])

    return ResidualPersistence(weights=weights, correlations=correlations, pair_counts=counts)


def latest_residuals(panel: pl.DataFrame, curve: MarketCurve) -> pl.DataFrame:
    """Return the most recent season's residual for every player who appeared in it.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        curve: The fitted curve.

    Returns:
        ``player_id`` and ``residual`` for the newest season only.
    """
    residuals = season_residuals(panel, curve)
    newest = residuals.get_column("season").max()
    return residuals.filter(pl.col("season") == newest).select("player_id", "residual")


def project(
    pool: pl.DataFrame,
    settings: Settings,
    curve: MarketCurve,
    persistence: ResidualPersistence,
    residuals: pl.DataFrame,
) -> pl.DataFrame:
    """Attach expected season points to every player in the pool being picked from.

    A player's residual comes from the previous season's export, joined by id. Absence there is
    the cold-start case — a promoted club's player or a new signing — and leaves him on the curve
    rather than penalised with an implicit zero. Presence with zero points is the opposite case
    and does count against him, because he was available and did not play.

    Args:
        pool: The pool to pick from, from :func:`~.data.load_latest_players`.
        settings: Supplies ``residual_weight`` when it overrides the measured persistence.
        curve: The fitted curve.
        persistence: Measured blend weights, used unless ``settings.residual_weight`` is set.
        residuals: Previous-season residuals from :func:`latest_residuals`.

    Returns:
        The pool with ``market_points``, ``residual``, ``residual_weight``,
        ``projected_points`` and ``points_per_million`` appended. ``projected_points`` is floored
        at zero: below the break-even value the straight line runs negative, but a player who
        never features scores nothing rather than losing points.
    """
    weight = (
        pl.lit(settings.residual_weight, dtype=pl.Float64)
        if settings.residual_weight is not None
        else persistence.weight_expression()
    )
    return (
        pool.join(residuals, on="player_id", how="left")
        .with_columns(
            curve.expected_points().alias("market_points"),
            pl.col("residual").fill_null(0.0),
            weight.alias("residual_weight"),
        )
        .with_columns(
            pl.max_horizontal(
                pl.col("market_points") + pl.col("residual_weight") * pl.col("residual"),
                pl.lit(0.0),
            ).alias("projected_points")
        )
        .with_columns(
            (pl.col("projected_points") / pl.col("market_value") * EUROS_PER_MILLION).alias(
                "points_per_million"
            )
        )
    )


def fit_and_project(
    panel: pl.DataFrame, pool: pl.DataFrame, settings: Settings
) -> tuple[pl.DataFrame, MarketCurve, ResidualPersistence]:
    """Fit on the panel and project the pool in one step.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        pool: The pool to pick from, from :func:`~.data.load_latest_players`.
        settings: Supplies the blend override, if any.

    Returns:
        The projected pool, the curve, and the measured persistence.
    """
    curve = fit_market_curve(panel, settings)
    persistence = estimate_residual_persistence(panel, curve)
    projected = project(pool, settings, curve, persistence, latest_residuals(panel, curve))
    return projected, curve, persistence
