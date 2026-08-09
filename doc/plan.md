# Plan: Optimal squad for the kicker-Managerspiel Classic

## Context

The goal of this repo is to output the optimal squad for the kicker-Managerspiel Classic (Bundesliga):
**15 players, of which only the 11 in the fixed 4-4-2 are treated as scoring**. The remaining 4 are
mandatory roster filler and should be bought as cheaply as the rules allow, so that as much of the
budget as possible flows into the XI.

The decision is **one-shot and irreversible**: squad and lineup are locked at the first kickoff and
stay fixed for every match of the season. In-season transfers are out of scope — there is no
re-optimization to plan for, and the entire value of the repo is in getting one pre-season answer right.

The binding difficulty is budget, not talent identification. A naive best XI picked purely on last
season's points costs **54.9M** against the **28.0M** that remains after the cheapest legal bench, so
the problem is genuinely *expected points per euro* under constraints, exactly as framed.

Two tasks, as identified:
1. **Projection** — estimate expected season points per player.
2. **Optimization** — pick the squad maximizing projected points subject to the rules.

## Rules that constrain the model

From `doc/rules.md` / `doc/faq.md`:

- Squad = exactly 15: **2 GK, 5 DEF, 5 MID, 3 FWD**. Formation fixed **4-4-2** → the scoring XI is
  1 GK, 4 DEF, 4 MID, 2 FWD, and the bench is therefore exactly **one player per position**.
- Budget **30M** (Bundesliga). Market values are set by the kicker editorial team and **do not change**
  during the season.
- **Max 3 players per real club**, checked at time of purchase.
- Scoring: 4 pts start / 2 pts sub-on; grade points are linear — `points = (3.5 − note) × 4`
  (1.0 → +10 … 6.0 → −10); goals 6/5/4/3 by GK/DEF/MID/FWD; assist 2; GK clean sheet 2;
  player of the match 3; yellow-red −3, red −6.
- Only complete, valid squads are scored at all, so all 15 slots must be filled regardless of whether
  the filler players are expected to contribute.

**Bench treatment (decided):** bench players are modelled as contributing **zero** and are minimized on
cost. Per the rules a bench player does score when his positional counterpart does not play at all, so
this is a deliberate simplification, retained as `bench_weight` (default 0) should it ever be worth
pricing in.

**The cheapest bench is 2.0M.** The pool contains 500k players in all four positions (25 GK, 16 DEF,
12 MID, 13 FWD), so 4 × 500k is attainable and leaves **28.0M** for the XI. This is a bound to assert
against, not an assumption to hard-code — see the club-cap coupling below.

## Data situation

`data/2026_08_09_spieler_daten.csv` (kicker Transfermarkt export, `;`-separated, UTF-8, 549 players,
18 clubs) provides: `ID, Vorname, Nachname, Angezeigter Name (kurz), Angezeigter Name, Verein,
Position, Marktwert, Punkte, Notendurchschnitt`. Filenames are date-stamped and the file may be
re-exported later, so the loader must select the newest `data/*_spieler_daten.csv` by default.

**What is missing is the denominator.** The export has season point *totals* but no appearances, so
`Punkte ÷ Spiele` — the statistic named in the request — cannot be computed from this file alone.
Phase 2 confirmed this is not recoverable by arithmetic either: grade points are linear, so
`points = 4·starts + 2·subs + n·f(mean grade) + extras`, and inverting it fails outright. At a grade
of 4.5 the per-appearance rate is `4 − 4 = 0`, so the points total carries *no* information about how
often the player featured; below 4.5 the implied count goes negative, and goals/assists/bonuses bias
it upward besides. A quarter of graded players imply more than a 34-match season. Appearances must be
ingested externally. Two further gaps:

- **224 of 549 players (41%) have 0 points**, and Phase 2 EDA shows the cohort is dominated by the
  three promoted clubs — Paderborn 97%, Elversberg 96%, Schalke 94% — which contribute 89 of the 224.
  Those players have **no** Bundesliga history rather than a bad one. The rest scatter at 11-43%
  across established clubs (fringe players, new signings). A zero here does not mean "bad", and the
  cohort reaches 4.5M in market value, so it cannot be discarded.
- `Notendurchschnitt` is confirmed (Phase 2 EDA) as the mean kicker grade: non-zero values span
  1.5-5.0 with mean 3.57. **`0.0` is a sentinel for "never graded", not a grade** — 244 players carry
  it, and feeding it to the scoring formula would imply `(3.5 − 0) × 4 = +14` points per appearance,
  better than a perfect 1.0. The projection must mask it rather than treat the column as numeric.

Appearance data must therefore be ingested separately, from kicker.de player pages (same data universe,
so grades and points reconcile against the export — preferred), with openligadb as a cross-check and
ligainsider for expected-starter and injury signals. Scraped pulls get cached to `data/` under the same
date-stamped convention so runs stay reproducible.

**Sequencing consequence:** the projection model ships in two passes — a baseline that needs only the
CSV (Phase 3), then the per-appearance refinement once appearances land (Phase 4). Phases 1–3 and 5 do
not block on scraping.

## Architecture

Flat module set under `src/kicker_manager_analysis/` — no subpackages, per the simplicity rule in
`AGENTS.md`:

| Module | Responsibility |
|---|---|
| `config.py` | `pydantic-settings` `Settings`: budget, squad/formation quotas, club cap, `bench_weight`, data dir |
| `data.py` | Polars load of the newest export, schema validation, canonical `Player` frame; optional appearance join |
| `scoring.py` | kicker scoring rules as pure functions (grade→points, goal points by position) — used to sanity-check `Punkte` and to decompose it |
| `projection.py` | expected season points per player |
| `optimize.py` | PuLP/CBC ILP: model build, lexicographic solve, top-K enumeration |
| `report.py` | XI + bench table, cost/points breakdown, selection-frequency summary |
| `cli.py` | argparse entry point running load → project → optimize → report |

Plus `notebooks/eda.py` as a marimo notebook for the exploratory work. Pydantic models for `Player`
and `Squad`; Polars for all tabular work.

## Phases

**Phase 1 — Scaffolding.** `pyproject.toml` (uv, src layout), ruff + mypy (strict) + pytest config,
dependencies: polars, pydantic, pydantic-settings, pulp, scikit-learn, marimo.

**Phase 2 — Data layer.** `data.py` + `config.py`. Loader picks the newest date-stamped export,
validates the schema, normalizes market values to euros and positions to a `Position` enum. Assert the
squad-composition rules are satisfiable from the pool. `scoring.py` implements the rules table.
EDA notebook resolves the `Notendurchschnitt` question and the zero-point cohort.

**Phase 3 — Baseline projection (CSV only).** Market value is itself the editorial forecast of a
player's season, so regress `Punkte` on `Marktwert` (and position) to recover the market's implied
points curve; the residual *is* the edge the optimizer exploits. Players with no Bundesliga history
fall back to the market-value-only prior — an honest treatment of the 41% cold-start cohort rather
than an implicit zero.

**Phase 4 — Per-appearance refinement.** Once appearances are ingested, decompose:

```
E[season points] = E[appearances] × E[points per appearance]
```

Estimate points-per-appearance from the prior season and **shrink it toward the position mean** in
proportion to sample size (empirical-Bayes / James–Stein). This matters: without shrinkage a player
with three lucky matches outranks a proven regular, and the optimizer — which hunts for cheap outliers —
will select precisely those noise-driven cases. Availability (`E[appearances]`) is the dominant variance
term and is where ligainsider's expected-starter and injury signals enter. It carries extra weight here
because a zero-weighted bench means a missed match is simply points forgone.

**Phase 5 — Optimizer.** Binary `x_p` (in the 15) and `s_p` (in the scoring XI) per player:

```
maximise   Σ_p  ŷ_p · ( s_p + λ · (x_p − s_p) )          λ = bench_weight, default 0
s.t.       s_p ≤ x_p                                      ∀p
           Σ x_p = 2 / 5 / 5 / 3                           by position
           Σ s_p = 1 / 4 / 4 / 2                           by position (4-4-2)
           Σ mw_p · x_p ≤ 30_000_000
           Σ_{p ∈ club c} x_p ≤ 3                          ∀ clubs c
```

1098 binaries — trivial for CBC.

Two implementation points that decide whether "cheapest bench" actually holds:

- **The club cap couples bench and XI.** A 500k filler still consumes one of his club's three slots, so
  the bench cannot be chosen separately and then bolted on — it must be solved jointly, as above.
- **λ=0 makes the bench cost-degenerate.** The solver is pushed to cheap fillers only insofar as the
  budget binds; once the optimal XI leaves any slack, *every* bench within budget is equally optimal and
  CBC may return an arbitrary one. Resolve with a **lexicographic two-stage solve**: maximize XI points
  to get `P*`, then re-solve minimizing `Σ mw_p · x_p` subject to `Σ ŷ_p s_p ≥ P* − tol`. That yields
  the cheapest squad among the point-optimal ones, exactly and without an ε-weight to tune.

Top-K alternative squads via no-good cuts (`Σ_{p ∈ S} x_p ≤ 14` for each squad `S` already found),
which is what makes the robustness pass cheap.

**Phase 6 — Robustness and reporting.** Because the choice is locked for a season, a single point
estimate is the wrong deliverable. Monte-Carlo sample `ŷ_p` from its predictive uncertainty, re-solve,
and report each player's **selection frequency** across draws — players appearing in nearly every
optimal squad are robust picks; those appearing rarely are artifacts of one noisy estimate. `report.py`
emits the recommended XI plus the four fillers, with cost, projected points, and selection frequency.

## Verification

- `uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest` (the `AGENTS.md` gate).
- Unit tests: `scoring.py` against every row of the rules table, including the negative grades and the
  per-position goal values; loader against a small fixture CSV.
- **Optimizer correctness is testable exactly** — on a hand-built pool of ~20 players the optimum can be
  enumerated by brute force and compared against CBC's answer. Also assert on the real pool that every
  returned squad satisfies all four constraint families (squad quotas, XI quotas, 30M budget,
  3-per-club).
- **Cheapest-bench assertion:** at λ=0 the four bench players must cost 2.0M in total, i.e. the bound
  above is met and not merely approached. A failure here means the lexicographic stage is missing or the
  club cap is forcing a more expensive filler — both worth surfacing explicitly rather than absorbing.
- Sanity: with the club cap and budget relaxed, the solver must reproduce the naive top-scorer XI
  (cost 54.9M) — a direct check that the objective and positional quotas are wired correctly.
- End-to-end: `uv run python -m kicker_manager_analysis.cli` prints a legal 15-player squad within 30M
  with the XI marked.

## Open items to confirm during implementation

- ~~The Ulreich reconciliation anomaly~~ — resolved in Phase 2: one start (4) + a 1.5 grade (8) +
  player of the match (3) = 15 exactly. The export is consistent with `scoring.py`.
- Which season `Punkte` / `Notendurchschnitt` describe. The magnitudes fit a full prior season
  (Kane 407 over ~34 matches), but the file itself does not state it.
- Whether kicker.de player pages expose appearances in a stable, scrapeable form; if not, Phase 4 falls
  back to openligadb lineups.
