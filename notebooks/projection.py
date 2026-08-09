import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def intro():
    import marimo as mo

    mo.md(
        """
        # The market curve, fitted on three seasons

        An earlier version of this notebook fitted the curve on a single export and concluded
        that points per euro *rises* with price, so value lived at the top of the market. That
        conclusion was an artefact and is retracted below.

        The export used carried the 2026/27 prices against the 2025/26 points. A price set after
        a season already knows how that season went, so regressing one on the other measures how
        kicker prices the past, not how a price predicts the future. With three seasons of
        properly paired data the picture changes on every count.

        Three questions here:

        1. How much did the mismatched pairing flatter the fit?
        2. How much of a player's over-performance carries into the next season?
        3. Given the answer to 2, where is there any edge at all?
        """
    )
    return (mo,)


@app.cell
def load(mo):
    from pathlib import Path

    import numpy as np
    import polars as pl

    from kicker_manager_analysis.config import Settings
    from kicker_manager_analysis.data import load_latest_players, load_panel
    from kicker_manager_analysis.projection import (
        fit_and_project,
        fit_market_curve,
        season_residuals,
    )
    from kicker_manager_analysis.scoring import Position

    settings = Settings(data_dir=Path(__file__).resolve().parents[1] / "data")
    panel = load_panel(settings.data_dir)
    pool = load_latest_players(settings)
    projected, curve, persistence = fit_and_project(panel, pool, settings)

    mo.md(
        f"Panel: **{panel.height}** player-seasons over "
        f"{sorted(panel.get_column('season').unique().to_list())}. Pool to pick from: "
        f"**{pool.height}** players. Slope **{curve.points_per_million:.1f}** points per million."
    )
    return (
        Position,
        curve,
        fit_market_curve,
        np,
        panel,
        persistence,
        pl,
        pool,
        projected,
        season_residuals,
        settings,
    )


@app.cell
def pairing_question(mo):
    mo.md("""
    ## 1. The mismatched pairing flattered the fit badly

    Fit the same functional form two ways: points against the price set *for* that season
    (forward-looking, what prediction needs), and points against the price set *after* it
    (retrospective, what the single export gave).
    """)
    return


@app.cell
def pairing_comparison(np, pl, pool, panel):
    from sklearn.linear_model import LinearRegression as _LinearRegression

    from kicker_manager_analysis.scoring import Position as _Position

    def _fit(frame):
        _mv = frame.get_column("market_value").to_numpy().astype(float) / 1e6
        _y = frame.get_column("points").to_numpy().astype(float)
        _pos = frame.get_column("position").to_numpy()
        _d = np.column_stack([(_pos == p.value) for p in _Position]).astype(float)
        _x = np.column_stack([_mv, _d])
        _m = _LinearRegression(fit_intercept=False).fit(_x, _y)
        return _m.score(_x, _y), _m.coef_[0], dict(zip(_Position, _m.coef_[1:], strict=True))

    _forward = _fit(panel)
    _retro = _fit(pool)
    pl.DataFrame(
        {
            "pairing": [
                "forward: points(S) on price(S)",
                "retrospective: points(S) on price(S+1)",
            ],
            "r2": [round(_forward[0], 3), round(_retro[0], 3)],
            "slope_pts_per_M": [round(_forward[1], 1), round(_retro[1], 1)],
            "forward_intercept": [
                round(_forward[2][_Position.FORWARD], 1),
                round(_retro[2][_Position.FORWARD], 1),
            ],
        }
    )
    return


@app.cell
def pairing_conclusion(mo):
    mo.md("""
    The retrospective fit looks far better and is useless: R² **0.67** against **0.43** on the
    same specification, and a slope inflated by roughly 40%. (The original Phase 3 fit reached
    0.75, flattered further by an exclusion the corrected pairing makes unnecessary.) Predicting
    an unrealised season is simply harder than describing a finished one, and 0.43 is the honest
    number.

    The retracted claim followed directly from that inflation. The old fit put every position's
    intercept strongly negative — up to -54 points for a forward — which implied a fixed toll of
    0.3-1.0M before a player returned anything, and therefore rising efficiency with price. On
    matched seasons the intercepts sit near zero, so **points per euro is close to flat** and a
    cheap player is not systematically poor value. What survives is the weaker statement that
    forwards carry the largest negative intercept, so they convert price to points slightly worse
    than defenders or midfielders.

    One thing the corrected pairing removes entirely: the single-season fit needed promoted-club
    players excluded, because their zero meant "was not in this league" rather than "did not
    play". On matched seasons a promoted club's players did play the season being measured, so
    every row is a valid observation and the exclusion is gone.
    """)
    return


@app.cell
def curve_table(Position, curve, pl):
    pl.DataFrame(
        {
            "position": [p.value for p in Position],
            "intercept": [round(curve.intercepts[p], 1) for p in Position],
            "break_even_M": [round(curve.break_even(p) / 1e6, 2) for p in Position],
            "residual_sd": [round(curve.residual_sd[p], 1) for p in Position],
        }
    )
    return


@app.cell
def persistence_question(mo):
    mo.md("""
    ## 2. Outfield over-performance does not carry over at all

    This is the parameter a single export could not identify, and the reason the panel was worth
    having. Blend weight was previously set to 0.5 by judgement. It can now be measured: regress
    a player's residual in one season on his residual in the next.
    """)
    return


@app.cell
def persistence_table(Position, persistence, pl):
    pl.DataFrame(
        {
            "position": [p.value for p in Position],
            "transitions": [persistence.pair_counts[p] for p in Position],
            "correlation": [round(persistence.correlations[p], 3) for p in Position],
            "weight_used": [round(persistence.weights[p], 3) for p in Position],
        }
    )
    return


@app.cell
def persistence_context(curve, np, panel, pl, season_residuals):
    from itertools import pairwise as _pairwise

    _res = season_residuals(panel, curve)
    _seasons = sorted(_res.get_column("season").unique().to_list())
    _pairs = pl.concat(
        panel.filter(pl.col("season") == a)
        .select("player_id", "position", "market_value", pl.col("points").alias("pts0"))
        .join(
            panel.filter(pl.col("season") == b).select("player_id", pl.col("points").alias("pts1")),
            on="player_id",
        )
        .join(
            _res.filter(pl.col("season") == a).select("player_id", "residual"),
            on="player_id",
        )
        .join(
            _res.filter(pl.col("season") == b).select(
                "player_id", pl.col("residual").alias("res1")
            ),
            on="player_id",
        )
        for a, b in _pairwise(_seasons)
    ).filter(pl.col("position") != "GOALKEEPER")

    def _corr(a, b):
        return round(
            float(
                np.corrcoef(
                    _pairs.get_column(a).to_numpy().astype(float),
                    _pairs.get_column(b).to_numpy().astype(float),
                )[0, 1]
            ),
            3,
        )

    pl.DataFrame(
        {
            "outfield relationship, season S to S+1": [
                "raw points",
                "price(S) against points(S+1)",
                "residual (points net of price)",
            ],
            "correlation": [
                _corr("pts0", "pts1"),
                _corr("market_value", "pts1"),
                _corr("residual", "res1"),
            ],
            "n": [_pairs.height] * 3,
        }
    )
    return


@app.cell
def persistence_conclusion(mo):
    mo.md("""
    Players are consistent — raw points correlate at **+0.59** from one season to the next. But
    once the price is known, what is left over correlates at **-0.04**. Every outfield position
    and every price band lands on zero independently, so this is not one thin slice of data.

    Read plainly: **for outfield players the kicker market value already contains everything last
    season had to say.** A player who beat his price by 100 points is no more likely to beat it
    again than anyone else. The measured weight is therefore 0, and the projection for an
    outfield player is simply the curve.

    Goalkeepers are the exception, and a large one at **+0.45**. That is exactly what a
    step-function outcome produces: a keeper's residual is mostly the persistent fact of being
    first choice, and first choices stay first choices. Modelling that properly is the next
    piece of work.

    The blend weight is now measured per position rather than assumed, so
    `Settings.residual_weight` exists only as an override for exploring how the answer moves.
    """)
    return


@app.cell
def club_question(mo):
    mo.md("""
    ## 3. The club effect is mean-reverting, so it stays out

    Club dummies lift in-sample R² noticeably, with big clubs coming out negative and mid-table
    clubs positive. The open question was whether that is a persistent squad-depth effect (deep
    squads rotate, so their players under-return against price — buy the mid-table club) or last
    season's over-performance being priced out (avoid it). The two readings invert the
    recommendation. One season could not separate them; the panel can.
    """)
    return


@app.cell
def club_persistence(curve, mo, np, panel, pl, season_residuals):
    from itertools import pairwise as _pairwise

    from sklearn.linear_model import LinearRegression as _LinearRegression

    _res = season_residuals(panel, curve).join(
        panel.select("player_id", "season", "club"), on=["player_id", "season"]
    )
    _club = _res.group_by("season", "club").agg(pl.col("residual").mean().alias("club_residual"))
    _seasons = sorted(_club.get_column("season").unique().to_list())
    _pairs = pl.concat(
        _club.filter(pl.col("season") == a)
        .select("club", pl.col("club_residual").alias("current"))
        .join(
            _club.filter(pl.col("season") == b).select(
                "club", pl.col("club_residual").alias("following")
            ),
            on="club",
        )
        for a, b in _pairwise(_seasons)
    )
    _x = _pairs.get_column("current").to_numpy()
    _y = _pairs.get_column("following").to_numpy()

    mo.md(
        f"""
        Across **{_pairs.height}** club transitions the slope is
        **{float(_LinearRegression().fit(_x[:, None], _y).coef_[0]):+.3f}** and the correlation
        **{float(np.corrcoef(_x, _y)[0, 1]):+.3f}**.

        Negative. A club whose players beat their prices one season tends to fall *below* them
        the next — the repricing reading, not the squad-depth one. So the club effect is not a
        persistent edge and does not belong in the projection; if anything it argues faintly
        against chasing last season's over-performing club. It stays out, now on evidence rather
        than caution.
        """
    )
    return


@app.cell
def edge_question(mo):
    mo.md("""
    ## 4. Where the edge actually is

    With outfield weights at zero the projection reduces to the curve, and the curve is close to
    linear through the origin. Points per euro by position, at prices spanning the pool:
    """)
    return


@app.cell
def edge_table(Position, curve, pl):
    _prices = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0]
    pl.DataFrame(
        {
            "market_value_M": _prices,
            **{
                p.value[:3].lower(): [
                    round(max(0.0, curve.intercepts[p] + curve.slope * v * 1e6) / v, 1)
                    for v in _prices
                ]
                for p in Position
            },
        }
    )
    return


@app.cell
def edge_top(pl, projected):
    projected.sort("points_per_million", descending=True).select(
        "name",
        "club",
        "position",
        "market_value",
        "market_points",
        "projected_points",
        "points_per_million",
    ).head(12).with_columns(
        pl.col("market_points").round(1),
        pl.col("projected_points").round(1),
        pl.col("points_per_million").round(1),
    )
    return


@app.cell
def edge_conclusion(mo):
    mo.md("""
    The efficiency table is nearly flat for outfield players — every euro buys roughly the same
    points wherever it is spent, which is the market doing its job. The only real spread is
    between positions: forwards convert worst, goalkeepers best.

    And the value ranking is **entirely goalkeepers**. That is not a quirk of the ranking; it is
    the one place the model has information the price does not, because the goalkeeper weight is
    the only non-zero one.

    This has a blunt implication for the optimizer. If outfield projections are just the curve,
    the choice among outfield players is close to degenerate — the solver will be nearly
    indifferent between any two ways of spending the same money, and its answer will be decided
    by small position-intercept differences rather than by real signal. The work that changes the
    recommended squad is therefore:

    - the goalkeeper model, where a measurable edge exists (next);
    - appearances, which is the one input that could break the outfield tie.

    It is worth stating the negative result plainly rather than burying it: on this evidence
    there is **no outfield stock-picking edge** available from the export alone.
    """)
    return


if __name__ == "__main__":
    app.run()
