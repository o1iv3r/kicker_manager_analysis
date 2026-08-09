import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def intro():
    import marimo as mo

    mo.md(
        """
        # The market curve

        `notebooks/eda.py` established what the export contains. This notebook builds the
        baseline projection on top of it and settles three questions:

        1. What functional form links market value to points?
        2. Which players should the curve be fitted on — and what breaks if that is got wrong?
        3. Where does the projection actually pay off, i.e. who is underpriced?

        The finding that matters for the optimizer is at the bottom: **points per euro rises
        with price**, so the cheap end of the pool is not where value lives.
        """
    )
    return (mo,)


@app.cell
def load(mo):
    from pathlib import Path

    import numpy as np
    import polars as pl

    from kicker_manager_analysis.config import Settings
    from kicker_manager_analysis.data import load_latest_players
    from kicker_manager_analysis.projection import (
        HAS_HISTORY,
        fit_market_curve,
        project,
        project_latest,
    )
    from kicker_manager_analysis.scoring import Position

    settings = Settings(data_dir=Path(__file__).resolve().parents[1] / "data")
    players = load_latest_players(settings)
    projected, curve = project_latest(players, settings)

    mo.md(
        f"Fitted on **{curve.sample_size}** of {players.height} players, excluding "
        f"{', '.join(curve.excluded_clubs)}. Slope **{curve.points_per_million:.1f}** points "
        f"per million euros."
    )
    return (
        HAS_HISTORY,
        Position,
        curve,
        fit_market_curve,
        np,
        pl,
        players,
        project,
        projected,
        settings,
    )


@app.cell
def form_question(mo):
    mo.md("""
    ## 1. The relationship is linear, not logarithmic

    Points-per-euro problems usually want a concave curve — diminishing returns on price. Here
    the opposite holds, and it is worth checking rather than assuming.
    """)
    return


@app.cell
def form_comparison(curve, np, pl, players):
    from sklearn.linear_model import LinearRegression
    from sklearn.model_selection import KFold, cross_val_score

    _fit = players.filter(~pl.col("club").is_in(curve.excluded_clubs))
    _mv = _fit.get_column("market_value").to_numpy().astype(float) / 1e6
    _y = _fit.get_column("points").to_numpy().astype(float)
    _pos = _fit.get_column("position").to_numpy()
    _dummies = np.column_stack([(_pos == p) for p in ["DEFENDER", "MIDFIELDER", "FORWARD"]]).astype(
        float
    )

    _specs = {
        "market value": _mv[:, None],
        "log(market value)": np.log(_mv)[:, None],
        "sqrt(market value)": np.sqrt(_mv)[:, None],
        "market value + position": np.column_stack([_mv, _dummies]),
        "per-position slope": np.column_stack([_mv, _dummies, _mv[:, None] * _dummies]),
    }
    pl.DataFrame(
        {
            "specification": list(_specs),
            "cv_r2": [
                round(
                    float(
                        cross_val_score(
                            LinearRegression(),
                            _x,
                            _y,
                            cv=KFold(5, shuffle=True, random_state=0),
                            scoring="r2",
                        ).mean()
                    ),
                    3,
                )
                for _x in _specs.values()
            ],
        }
    )
    return


@app.cell
def form_conclusion(mo):
    mo.md("""
    Linear beats log by a wide margin, and adding position intercepts is worth another ~0.03.
    Letting each position have its own *slope* adds nothing out of sample, so the model keeps
    one shared slope — which also avoids handing the 24 goalkeepers with history their own
    poorly determined line.

    The linear form with a **negative intercept** is what drives everything downstream: a fixed
    amount of market value buys no points at all, and only the value above that threshold
    converts.
    """)
    return


@app.cell
def sample_question(mo):
    mo.md("""
    ## 2. The fit sample is the part that is easy to get wrong

    A player with zero points and no grade never appeared. That reads as missing data, which
    argues for dropping those rows. For promoted clubs that is right — they were not in the
    league. For an established club it is **wrong**: there, not playing is the outcome the
    curve is supposed to predict.

    Goalkeepers show what the mistake costs, because their pool is mostly backups.
    """)
    return


@app.cell
def sample_goalkeepers(HAS_HISTORY, Position, pl, players):
    players.filter(pl.col("position") == Position.GOALKEEPER.value).with_columns(
        (pl.col("market_value") // 500_000 * 500_000).alias("price_bucket")
    ).group_by("price_bucket").agg(
        pl.len().alias("keepers"),
        HAS_HISTORY.sum().alias("ever_played"),
        pl.col("points").mean().round(1).alias("mean_points"),
    ).sort("price_bucket")
    return


@app.cell
def sample_comparison(HAS_HISTORY, Position, curve, fit_market_curve, mo, players, settings):
    _history_only = fit_market_curve(players.filter(HAS_HISTORY), settings)
    _cheap = 500_000

    def _value_of_a_cheap_keeper(fitted) -> float:
        _points = fitted.intercepts[Position.GOALKEEPER] + fitted.slope * _cheap
        return max(0.0, _points) / (_cheap / 1e6)

    mo.md(
        f"""
        23 of the 25 goalkeepers priced at 500k never played. Fit on only those who did, and
        the goalkeeper intercept turns **positive** — the curve then claims a 500k keeper
        returns {_value_of_a_cheap_keeper(_history_only):.0f} points per million, the best
        value anywhere in the pool. The optimizer would have bought that keeper every time.

        Keeping the informative zeros gives {_value_of_a_cheap_keeper(curve):.0f} points per
        million instead, and moves the goalkeeper intercept from
        **{_history_only.intercepts[Position.GOALKEEPER]:+.1f}** to
        **{curve.intercepts[Position.GOALKEEPER]:+.1f}**.

        Promoted clubs are identified from the data — the share of each squad with league
        history — rather than by name, so this keeps working next season. The gap is not close:
        the three promoted sides sit at 3-6%, every other club above 57%.
        """
    )
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
def curve_reading(mo):
    mo.md("""
    Read the break-even column as the price of simply being in the squad. A forward has to cost
    over 1.0M before he is expected to return anything; a goalkeeper only 0.29M, because
    keepers who play at all accumulate appearance points steadily.

    Goalkeepers carry a much wider residual spread (51 against ~38) — their outcome is close to
    binary, first choice or not, and market value alone does not resolve which.
    """)
    return


@app.cell
def efficiency_question(mo):
    mo.md("""
    ## 3. Value lives at the top of the market, not the bottom

    This is the result that shapes the optimizer. Because the intercept is negative, points per
    euro *rises* with price and flattens out — the opposite of the bargain-hunting intuition.
    """)
    return


@app.cell
def efficiency_table(Position, curve, pl):
    pl.DataFrame(
        {
            "market_value_M": [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 10.0],
            **{
                p.value[:3].lower(): [
                    round(max(0.0, curve.intercepts[p] + curve.slope * v * 1e6) / v, 1)
                    for v in [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 10.0]
                ]
                for p in Position
            },
        }
    )
    return


@app.cell
def efficiency_conclusion(mo):
    mo.md("""
    A 500k outfield player is projected to return **nothing**. The same euro spent at 3M returns
    40-45 points per million. That settles a question the plan left open: the four bench slots
    should be filled with the cheapest bodies available not merely because the bench does not
    score, but because cheap players are poor value even when they do play.

    It also means the market curve *on its own* gives a degenerate optimum — if points were
    exactly linear in price, every way of spending the budget would be equally good. So the
    entire edge sits in the residual.
    """)
    return


@app.cell
def residual_question(mo):
    mo.md("""
    ## 4. How far to trust last season

    `projected = curve + residual_weight x (observed - curve)`. The weight is **not
    identifiable from one export**: separating a player's persistent over-performance from one
    season of luck needs a second season, or the appearance counts of Phase 4. The default of
    0.5 splits the difference; here is what it costs to be wrong.
    """)
    return


@app.cell
def residual_sensitivity(np, pl, players, project, settings):
    from kicker_manager_analysis.projection import fit_market_curve as _fit_curve

    _rows = []
    _baseline: set[str] = set()
    for _w in [0.0, 0.25, 0.5, 0.75, 1.0]:
        _s = settings.model_copy(update={"residual_weight": _w})
        _p = project(players, _s, _fit_curve(players, _s))
        _top = _p.sort("projected_points", descending=True).head(11)
        _names = set(_top.get_column("name"))
        if _w == 0.0:
            _baseline = _names
        _rows.append(
            {
                "residual_weight": _w,
                "top11_points": round(float(_top.get_column("projected_points").sum())),
                "top11_cost_M": round(float(_top.get_column("market_value").sum()) / 1e6, 1),
                "shared_with_curve_only": len(_names & _baseline),
                "spread_of_projections": round(
                    float(np.std(_p.get_column("projected_points").to_numpy())), 1
                ),
            }
        )
    pl.DataFrame(_rows)
    return


@app.cell
def residual_conclusion(mo):
    mo.md("""
    This knob matters more than it looks. Only **6 of the 11** best players survive the move
    from weight 0 to weight 1, and the turnover is systematic rather than random: trusting last
    season drops expensive Bayern players who cost more than they returned (Musiala, Tah) and
    pulls in cheap keepers and defenders with large positive residuals (Kobel, Heuer Fernandes,
    Nicolas, Coufal). The projection spread widens from 55 to 64 points, which is what governs
    how hard the optimizer chases a cheap outlier.

    Note the two ends are both degenerate in their own way. At weight 0 every player sits
    exactly on the curve, so any two squads costing the same are equally good and the solve has
    nothing to choose between them. At weight 1 a single lucky season is taken at face value —
    precisely the case the optimizer is built to hunt for.

    The top-11 cost falls from 64.2M to 57.1M as the weight rises, but both are far beyond the
    ~28M actually available, so the budget still binds hard either way.

    Phase 4 replaces this knob with a shrinkage factor estimated from appearances, which is the
    principled version of the same blend.
    """)
    return


@app.cell
def underpriced_question(mo):
    mo.md("""
    ## 5. Who the model likes

    Ranking by residual per euro is what the optimizer will effectively do, so it is worth
    looking at directly before trusting a solve.
    """)
    return


@app.cell
def underpriced_table(pl, projected):
    projected.filter(pl.col("has_history")).with_columns(
        (pl.col("residual") / pl.col("market_value") * 1e6).alias("residual_per_M")
    ).sort("residual_per_M", descending=True).select(
        "name", "club", "position", "market_value", "points", "market_points", "residual_per_M"
    ).head(15).with_columns(pl.col("market_points").round(0), pl.col("residual_per_M").round(1))
    return


@app.cell
def underpriced_conclusion(mo):
    mo.md("""
    Goalkeepers dominate this list, and that is a warning rather than a recommendation. A keeper
    priced at 1.0M who scored 150+ was a first choice last season who is priced as a backup
    now — which usually means the editorial team knows something about the depth chart that the
    export does not carry. Only one goalkeeper makes the XI, so the exposure is limited, but it
    is the clearest case where an availability signal is needed.

    Two questions this notebook cannot answer, both carried into Phase 4:

    - **Club effects are large and their sign is ambiguous.** Adding club dummies lifts R² from
      0.75 to 0.80, with Bayern at -25 and Hoffenheim at +25 points. That is either a
      persistent squad-rotation effect (deep squads share minutes, so their players
      under-return against price) or last season's over-performance being priced out — the
      first says buy Hoffenheim, the second says avoid it. One season cannot separate them, so
      the club effect is deliberately **left out** of the projection.
    - **Availability.** Every large positive residual here is a bet that the player keeps his
      role. That is exactly what `E[appearances]` is for.
    """)
    return


if __name__ == "__main__":
    app.run()
