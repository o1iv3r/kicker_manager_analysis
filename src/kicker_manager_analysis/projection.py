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

Goalkeepers do not take the curve at all. Keeping is a step function on *who plays* rather than a
line on price, because keepers are almost never substituted, so :class:`GoalkeeperModel` replaces
the curve for them. With the appearance data the step is measured directly rather than inferred:
a club's most expensive keeper plays 27.9 matches on average and his deputy 4.0. Fitting that step
explicitly absorbs about half the goalkeeper residual persistence — it falls from +0.45 to +0.24 —
which is the sense in which the old blend weight was a proxy for this model.
"""

from enum import StrEnum
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


class Registration(StrEnum):
    """What last season says about a player, which is not the same as what his points say.

    A zero in the points column means two opposite things depending on why it is there, and only
    membership of the previous season's frame separates them.
    """

    PLAYED = "PLAYED"
    """In the league last season and featured at least once."""

    REGISTERED = "REGISTERED"
    """In the league last season and never featured — evidence against him."""

    ABSENT = "ABSENT"
    """Not in the league last season: a promotion, a signing from abroad, or a youth player."""


REGISTRATION_DTYPE: Final = pl.Enum([status.value for status in Registration])

FIRST_CHOICE_APPEARANCES: Final = 17
"""Appearances above which a goalkeeper counts as his club's number one for the season.

Half of a 34-match season. The cut is not delicate: keepers do not cluster near it, because the
role barely rotates. A club's most expensive keeper averages 27.9 appearances and the next 4.0,
so almost any threshold between them classifies the same keepers.
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


class GoalkeeperModel(BaseModel):
    """The step function that replaces the curve for goalkeepers.

    A keeper's season is decided by whether he is his club's number one, which the within-club
    price rank predicts and the absolute price does not. Expected points are therefore

        P(number one | rank) x points of a number one  +  P(deputy | rank) x points of a deputy

    Fitting points on absolute price instead is what produced the Phase 3 anomaly where a 500k
    keeper looked like the pool's best value: such a keeper is almost always somebody's deputy.

    Attributes:
        first_choice_probability: Share of keepers at each within-club price rank who played at
            least :data:`FIRST_CHOICE_APPEARANCES` matches. Ranks beyond the deepest fitted key
            fall back to the last one.
        first_choice_intercept: Points a number one scores at a nominal zero price.
        first_choice_slope: Additional points per euro of market value, for a number one.
        deputy_points: Mean points of a keeper who was not his club's number one.
        sample_size: Number of goalkeeper-seasons fitted on.
    """

    model_config = ConfigDict(frozen=True)

    first_choice_probability: dict[int, float]
    first_choice_intercept: float
    first_choice_slope: float
    deputy_points: float
    sample_size: int = Field(gt=0)

    @property
    def first_choice_points_per_million(self) -> float:
        """Points a number one gains per additional million of market value.

        Essentially zero in the fitted data: among the 49 number ones in the panel, points
        correlate with price at -0.03. The cheapest number one of each season scored 257, 261 and
        230 against 208, 213 and 254 for the dearest, so paying up for a keeper buys nothing the
        rank has not already given.
        """
        return self.first_choice_slope * EUROS_PER_MILLION

    def expected_points(self) -> pl.Expr:
        """Return the expression evaluating the model over a keeper frame.

        Returns:
            A Polars expression in ``keeper_rank`` and ``market_value``. Rows whose rank exceeds
            the fitted ranks take the deepest fitted probability, which is the "clearly not the
            number one" case.
        """
        deepest = max(self.first_choice_probability)
        probability = (
            pl.col("keeper_rank")
            .clip(upper_bound=deepest)
            .replace_strict(self.first_choice_probability, return_dtype=pl.Float64)
        )
        first_choice = self.first_choice_intercept + self.first_choice_slope * pl.col(
            "market_value"
        )
        return probability * first_choice + (1 - probability) * self.deputy_points


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


MAX_FITTED_KEEPER_RANK: Final = 3
"""Ranks kept distinct when fitting; deeper keepers are pooled into the last one.

A club carries two or three keepers worth pricing, and the fourth is indistinguishable from the
third — both play under two matches a season.
"""


def with_keeper_rank(players: pl.DataFrame) -> pl.DataFrame:
    """Attach each keeper's within-club price rank.

    Rank is computed within club *and position*, which is the whole point: ranking a keeper
    against his club's outfield players puts him eighth or fifteenth and destroys the signal.
    Ties break by order of appearance, so a club pricing two keepers identically still yields a
    number one — arbitrary, but such a club gets near-equal probabilities from the model anyway.

    Args:
        players: A pool or a panel. A ``season`` column, if present, joins the partition so that
            one season's prices cannot rank against another's.

    Returns:
        The frame with an Int32 ``keeper_rank`` column, null for outfield players.
    """
    partition = ["club", "position"]
    if "season" in players.columns:
        partition.insert(0, "season")
    return players.with_columns(
        pl.when(pl.col("position") == Position.GOALKEEPER.value)
        .then(pl.col("market_value").rank("ordinal", descending=True).over(partition))
        .otherwise(None)
        .cast(pl.Int32)
        .alias("keeper_rank")
    )


def fit_goalkeeper_model(panel: pl.DataFrame) -> GoalkeeperModel:
    """Fit the goalkeeper step function on the panel.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`, carrying ``appearances``.

    Returns:
        The fitted model.

    Raises:
        ValueError: If the panel holds no goalkeeper who reached first-choice appearances, which
            would leave the first-choice branch unidentified.
    """
    keepers = with_keeper_rank(panel).filter(pl.col("position") == Position.GOALKEEPER.value)
    first_choice = keepers.filter(pl.col("appearances") >= FIRST_CHOICE_APPEARANCES)
    if first_choice.is_empty():
        raise ValueError("cannot fit the goalkeeper model, no first-choice keeper in the panel")

    probability: dict[int, float] = {}
    for rank in range(1, MAX_FITTED_KEEPER_RANK + 1):
        at_rank = keepers.filter(
            pl.col("keeper_rank") == rank
            if rank < MAX_FITTED_KEEPER_RANK
            else pl.col("keeper_rank") >= rank
        )
        probability[rank] = (
            float(np.mean(at_rank.get_column("appearances").to_numpy() >= FIRST_CHOICE_APPEARANCES))
            if at_rank.height
            else 0.0
        )

    millions = first_choice.get_column("market_value").to_numpy().astype(float) / EUROS_PER_MILLION
    fitted = LinearRegression().fit(
        millions[:, None], first_choice.get_column("points").to_numpy().astype(float)
    )
    deputies = keepers.filter(pl.col("appearances") < FIRST_CHOICE_APPEARANCES)
    return GoalkeeperModel(
        first_choice_probability=probability,
        first_choice_intercept=float(fitted.intercept_),
        first_choice_slope=float(fitted.coef_[0]) / EUROS_PER_MILLION,
        deputy_points=float(np.mean(deputies.get_column("points").to_numpy()))
        if deputies.height
        else 0.0,
        sample_size=keepers.height,
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

    millions = panel.get_column("market_value").to_numpy().astype(float) / EUROS_PER_MILLION
    points = panel.get_column("points").to_numpy().astype(float)
    blocks = [millions[:, None], position_dummies]
    if len(ordered_seasons) > 1:
        # A one-season panel needs no season dummies, and asking for the empty set of them
        # would leave nothing to stack. It arises whenever a backtest holds a season out.
        blocks.append(
            np.column_stack([(seasons == season) for season in ordered_seasons[1:]]).astype(float)
        )
    design = np.hstack(blocks)
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


def baseline_expression(
    curve: MarketCurve, goalkeepers: GoalkeeperModel, season: int | None = None
) -> pl.Expr:
    """Return expected points before any residual blend.

    Outfield players take the market curve; goalkeepers take the step model instead, because
    price does not describe a keeper's season and rank does.

    Args:
        curve: The fitted outfield curve.
        goalkeepers: The fitted goalkeeper step model.
        season: Season to price for, passed to the curve. Omit for an average season.

    Returns:
        A Polars expression in ``position``, ``market_value`` and ``keeper_rank``.
    """
    return (
        pl.when(pl.col("position") == Position.GOALKEEPER.value)
        .then(goalkeepers.expected_points())
        .otherwise(curve.expected_points(season))
    )


def season_residuals(
    panel: pl.DataFrame, curve: MarketCurve, goalkeepers: GoalkeeperModel
) -> pl.DataFrame:
    """Return each player-season's departure from what the model expected of him.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        curve: The fitted curve.
        goalkeepers: The fitted goalkeeper step model.

    Returns:
        ``player_id``, ``season``, ``position``, ``appearances`` and ``residual``.
    """
    ranked = with_keeper_rank(panel)
    per_season = []
    for season in sorted(panel.get_column("season").unique().to_list()):
        per_season.append(
            ranked.filter(pl.col("season") == season)
            .with_columns(
                (pl.col("points") - baseline_expression(curve, goalkeepers, int(season))).alias(
                    "residual"
                )
            )
            .select("player_id", "season", "position", "appearances", "residual")
        )
    return pl.concat(per_season)


def estimate_residual_persistence(
    panel: pl.DataFrame, curve: MarketCurve, goalkeepers: GoalkeeperModel
) -> ResidualPersistence:
    """Measure how far a player's residual predicts his residual the following season.

    This is the quantity a single export cannot identify. Weights are clamped to [0, 1]: the
    outfield estimates land slightly negative, which would mean over-performance predicts
    under-performance, and at these correlations that is noise rather than mean reversion worth
    acting on. The unclamped correlation is kept so the clamping stays visible.

    Residuals are taken against the full model, goalkeeper step included, so the weight measures
    only what that model has *not* already explained. Fitting the step explicitly is why the
    goalkeeper estimate falls from +0.45 to +0.24 against the curve-only residuals.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        curve: The fitted curve.
        goalkeepers: The fitted goalkeeper step model.

    Returns:
        The estimated persistence per position.
    """
    residuals = season_residuals(panel, curve, goalkeepers)
    seasons = sorted(residuals.get_column("season").unique().to_list())
    if len(seasons) < 2:
        # Nothing carries over from a season to itself. A single-season panel arises when a
        # backtest holds one of two seasons out, and must fall back to the curve alone.
        return ResidualPersistence(
            weights=dict.fromkeys(Position, 0.0),
            correlations=dict.fromkeys(Position, 0.0),
            pair_counts=dict.fromkeys(Position, 0),
        )
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
        if current.std() == 0.0 or following.std() == 0.0:
            # A position whose residuals never move carries no correlation to measure, and
            # asking for one returns a NaN that would silently poison every later comparison.
            weights[position] = 0.0
            correlations[position] = 0.0
            continue
        slope = float(LinearRegression().fit(current[:, None], following).coef_[0])
        weights[position] = min(max(slope, 0.0), 1.0)
        correlations[position] = float(np.corrcoef(current, following)[0, 1])

    return ResidualPersistence(weights=weights, correlations=correlations, pair_counts=counts)


def latest_residuals(
    panel: pl.DataFrame, curve: MarketCurve, goalkeepers: GoalkeeperModel
) -> pl.DataFrame:
    """Return the most recent season's residual for every player who was in the league for it.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        curve: The fitted curve.
        goalkeepers: The fitted goalkeeper step model.

    Returns:
        ``player_id``, ``residual`` and ``previous_appearances`` for the newest season only. The
        appearances distinguish a player who was registered and did not feature from one who was
        not in the league at all — the two look identical in a points total but mean opposite
        things, and only the frame's *membership* can tell them apart. The column is named for the
        season it describes so that it cannot collide with a pool's own appearance count.
    """
    residuals = season_residuals(panel, curve, goalkeepers)
    newest = residuals.get_column("season").max()
    return residuals.filter(pl.col("season") == newest).select(
        "player_id", "residual", pl.col("appearances").alias("previous_appearances")
    )


def project(
    pool: pl.DataFrame,
    settings: Settings,
    curve: MarketCurve,
    goalkeepers: GoalkeeperModel,
    persistence: ResidualPersistence,
    residuals: pl.DataFrame,
) -> pl.DataFrame:
    """Attach expected season points to every player in the pool being picked from.

    A player's residual comes from the previous season, joined by id, and the join splits the pool
    three ways — the distinction the single-season export could not draw:

    - **played**: in the league last season and featured, so his residual is a real observation;
    - **registered**: in the league and never featured, which counts against him, because he was
      available and was not picked;
    - **absent**: not in the league at all, the cold-start case, left on the model's prior rather
      than penalised with an implicit zero he did not earn.

    Args:
        pool: The pool to pick from, from :func:`~.data.load_latest_players`.
        settings: Supplies ``residual_weight`` when it overrides the measured persistence.
        curve: The fitted curve.
        goalkeepers: The fitted goalkeeper step model.
        persistence: Measured blend weights, used unless ``settings.residual_weight`` is set.
        residuals: Previous-season residuals from :func:`latest_residuals`.

    Returns:
        The pool with ``keeper_rank``, ``registration``, ``market_points``, ``residual``,
        ``residual_weight``, ``projected_points`` and ``points_per_million`` appended.
        ``projected_points`` is floored at zero: below the break-even value the straight line runs
        negative, but a player who never features scores nothing rather than losing points.
    """
    weight = (
        pl.lit(settings.residual_weight, dtype=pl.Float64)
        if settings.residual_weight is not None
        else persistence.weight_expression()
    )
    return (
        with_keeper_rank(pool)
        .join(residuals, on="player_id", how="left")
        .with_columns(
            pl.when(pl.col("residual").is_null())
            .then(pl.lit(Registration.ABSENT.value))
            .when(pl.col("previous_appearances") > 0)
            .then(pl.lit(Registration.PLAYED.value))
            .otherwise(pl.lit(Registration.REGISTERED.value))
            .cast(REGISTRATION_DTYPE)
            .alias("registration"),
            baseline_expression(curve, goalkeepers).alias("market_points"),
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
        .drop("previous_appearances")
    )


class FittedModel(BaseModel):
    """Everything fitted on the panel, kept together because the pieces are interdependent.

    The persistence weights are measured against residuals that already include the goalkeeper
    model, so pairing a curve with someone else's weights would misstate both.

    Attributes:
        curve: The outfield market curve.
        goalkeepers: The goalkeeper step model.
        persistence: Residual blend weights per position.
    """

    model_config = ConfigDict(frozen=True)

    curve: MarketCurve
    goalkeepers: GoalkeeperModel
    persistence: ResidualPersistence


def fit_model(panel: pl.DataFrame, settings: Settings) -> FittedModel:
    """Fit the curve, the goalkeeper model and the blend weights on one panel.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        settings: Passed to the curve fit.

    Returns:
        The fitted model.
    """
    curve = fit_market_curve(panel, settings)
    goalkeepers = fit_goalkeeper_model(panel)
    return FittedModel(
        curve=curve,
        goalkeepers=goalkeepers,
        persistence=estimate_residual_persistence(panel, curve, goalkeepers),
    )


def fit_and_project(
    panel: pl.DataFrame, pool: pl.DataFrame, settings: Settings
) -> tuple[pl.DataFrame, FittedModel]:
    """Fit on the panel and project the pool in one step.

    Args:
        panel: Player-seasons from :func:`~.data.load_panel`.
        pool: The pool to pick from, from :func:`~.data.load_latest_players`.
        settings: Supplies the blend override, if any.

    Returns:
        The projected pool and the fitted model.
    """
    model = fit_model(panel, settings)
    projected = project(
        pool,
        settings,
        model.curve,
        model.goalkeepers,
        model.persistence,
        latest_residuals(panel, model.curve, model.goalkeepers),
    )
    return projected, model
