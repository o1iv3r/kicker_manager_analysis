import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def intro():
    import marimo as mo

    mo.md(
        """
        # Exploring the kicker player export

        Two questions have to be settled before any projection is built on this file:

        1. What is `Notendurchschnitt`, and what does the value `0.0` mean?
        2. The 41% of players with zero points — are they bad, or simply new?

        A third question is answered along the way: can the *number of appearances* be
        recovered from the export, which would supply the denominator the points-per-game
        statistic needs?
        """
    )
    return (mo,)


@app.cell
def load(mo):
    from pathlib import Path

    import polars as pl

    from kicker_manager_analysis.config import Settings
    from kicker_manager_analysis.data import latest_export, load_latest_players

    settings = Settings(data_dir=Path(__file__).resolve().parents[1] / "data")
    players = load_latest_players(settings)

    mo.md(f"Loaded **{players.height}** players from `{latest_export(settings.data_dir).name}`.")
    return pl, players


@app.cell
def grade_question(mo):
    mo.md("""
    ## 1. `Notendurchschnitt` is the mean kicker grade, and `0.0` is a sentinel

    If the column were a grade average, its non-zero values should sit on the kicker
    scale (1.0 best, 6.0 worst) and cluster near the neutral 3.5.
    """)
    return


@app.cell
def grade_distribution(pl, players):
    graded = players.filter(pl.col("grade_average") > 0)
    graded.get_column("grade_average").describe()
    return (graded,)


@app.cell
def grade_conclusion(mo, pl, players):
    ungraded = players.filter(pl.col("grade_average") == 0).height
    played_but_ungraded = players.filter(
        (pl.col("grade_average") == 0) & (pl.col("points") != 0)
    ).height

    mo.md(
        f"""
        Confirmed: the non-zero values span 1.5-5.0 and centre on ~3.57, which is the kicker
        grade distribution. So `0.0` is **not a grade** — it is a sentinel for *never graded*,
        carried by {ungraded} players.

        This is a trap worth naming. Feeding `0.0` into the scoring formula as a grade implies
        `(3.5 - 0) * 4 = +14` points per appearance, i.e. better than a perfect 1.0. Any
        projection must mask these rows rather than treat the column as numeric.

        {played_but_ungraded} players scored points while never being graded — they appeared,
        but always for under the ~25 minutes a grade requires.
        """
    )
    return


@app.cell
def appearances_question(mo):
    mo.md(r"""
    ## 2. Appearances cannot be recovered from this file

    Grade points are linear in the grade, so summing them over a season collapses neatly
    onto the mean:

    $$\text{points} = 4\,n_{\text{start}} + 2\,n_{\text{sub}}
      + n_{\text{graded}} \cdot f(\overline{\text{grade}}) + \text{extras}$$

    That is tempting: if appearances were the only unknown, dividing points by the
    per-appearance rate would recover them. It does not work.
    """)
    return


@app.cell
def implied_appearances(graded, pl):
    implied = graded.with_columns(
        (pl.col("points") / (4 + (3.5 - pl.col("grade_average")) * 4)).alias("implied_apps")
    )
    implied.select(
        pl.len().alias("graded_players"),
        (pl.col("implied_apps") > 34).sum().alias("above_a_34_match_season"),
        (pl.col("implied_apps") < 0).sum().alias("negative"),
        pl.col("implied_apps").is_infinite().sum().alias("infinite"),
    )
    return


@app.cell
def appearances_conclusion(mo):
    mo.md("""
    The estimator breaks in three ways:

    - **It diverges at a grade of 4.5.** There the per-appearance rate is `4 - 4 = 0`: the
      start bonus exactly cancels the grade penalty, so a player's points total says
      nothing at all about how often he played. Below 4.5 the rate turns negative and the
      implied count flips sign.
    - **Goals, assists, clean sheets and player-of-the-match awards are folded into the
      same total**, so the estimate is biased upward — badly for strikers.
    - A quarter of graded players imply more than a 34-match season.

    Appearances therefore have to be ingested from kicker.de or openligadb, as planned.
    The export gives a season *total* and no way to decompose it.
    """)
    return


@app.cell
def anomaly(mo, pl, players):
    from kicker_manager_analysis.scoring import (
        PLAYER_OF_THE_MATCH_POINTS,
        STARTING_LINEUP_POINTS,
        points_for_grade,
    )

    ulreich = players.filter(pl.col("name") == "Sven Ulreich").row(0, named=True)
    reconstructed = STARTING_LINEUP_POINTS + points_for_grade(1.5) + PLAYER_OF_THE_MATCH_POINTS

    mo.md(
        f"""
        ### The one reconciliation that looked wrong

        Sven Ulreich shows **{ulreich["points"]} points** at an average grade of
        **{ulreich["grade_average"]}**, which initially looked inconsistent. It is not: one
        start ({STARTING_LINEUP_POINTS}) plus a 1.5 grade ({points_for_grade(1.5):.0f}) plus
        player of the match ({PLAYER_OF_THE_MATCH_POINTS}) is exactly {reconstructed:.0f}.
        The rules in `scoring.py` reproduce the export.
        """
    )
    return


@app.cell
def cohort_question(mo):
    mo.md("""
    ## 3. The zero-point cohort is mostly promoted clubs

    A zero in `Punkte` could mean "played badly", "never played", or "was not in this
    league last season". The three readings call for very different treatment.
    """)
    return


@app.cell
def cohort_by_club(pl, players):
    players.with_columns((pl.col("points") == 0).alias("zero")).group_by("club").agg(
        pl.len().alias("players"),
        pl.col("zero").sum().alias("zeros"),
        (pl.col("zero").mean() * 100).round(0).alias("pct_zero"),
    ).sort("pct_zero", descending=True)
    return


@app.cell
def cohort_conclusion(mo):
    mo.md("""
    The pattern is unambiguous. Paderborn (97%), Elversberg (96%) and Schalke (94%) are
    the promoted clubs: their players have **no Bundesliga history**, not a bad one. Those
    three carry 89 of the 224 zeros. The rest scatter at 11-43% across established clubs —
    fringe players and new signings.

    Zero-point players are cheaper (median 1.2M against 1.8M) but run up to 4.5M. A high
    market value with zero points is the editorial team stating plainly that it expects
    the player to feature. That is the signal the cold-start prior should lean on, and it
    is why the baseline model regresses points on market value rather than treating a
    missing history as a zero.
    """)
    return


if __name__ == "__main__":
    app.run()
