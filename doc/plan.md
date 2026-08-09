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

The goalkeeper finding in Phase 3b sharpens this for the one position where cover looks most
valuable. A cheap reserve keeper is nearly always his own club's number two, and keepers are almost
never substituted, so he scores ~0 whether or not our starting keeper plays. The bench slot cannot be
salvaged by spending on it — buying a *second* number one would burn several million for a player who
scores only if the first fails, which is the worst trade on the board.

**The cheapest bench is 2.0M.** The pool contains 500k players in all four positions (25 GK, 16 DEF,
12 MID, 13 FWD), so 4 × 500k is attainable and leaves **28.0M** for the XI. This is a bound to assert
against, not an assumption to hard-code — see the club-cap coupling below.

## Data situation

Four exports are available (kicker Transfermarkt export, `;`-separated, UTF-8, identical schema:
`ID, Vorname, Nachname, Angezeigter Name (kurz), Angezeigter Name, Verein, Position, Marktwert,
Punkte, Notendurchschnitt`). **`ID` is stable across files** — `pl-k00030669` is Manuel Neuer in all
four — which is what makes the panel below possible.

### What season each file describes

The club list gives the squads for the *upcoming* season; `Punkte` and `Notendurchschnitt` give the
*completed* one. Verified against known promotions: St. Pauli (up for 2024/25) already carry real
points in the 2024 file, Köln and HSV (up for 2025/26) already carry real points in the 2025 file.

| file | squads for | results from | rows |
|---|---|---|---|
| `2023_spieler_daten.csv` | 2023/24 | **2023/24** | 488 |
| `2024_spieler_daten.csv` | 2024/25 | **2024/25** | 487 |
| `2025_spieler_daten.csv` | 2025/26 | **2025/26** | 477 |
| `2026_08_09_spieler_daten.csv` | 2026/27 | 2025/26 *(repeat)* | 549 |

The last row is the important one. All **356** players common to the 2025 and 2026 files carry
byte-identical `Punkte` *and* `Notendurchschnitt`, including the 22 who changed clubs between them
(Prömel 182 at Hoffenheim then 182 at Stuttgart). By contrast 2023→2024 shares only 10 of 290 and
2024→2025 only 25 of 286. So the 2026 file is **not** a fourth season of results: it is the 2026/27
price list bolted onto the 2025/26 results.

That yields three seasons of correctly paired `(price for season S, points scored in season S)` —
2023/24, 2024/25, 2025/26 — plus the 2026/27 prices to predict against.

### This invalidates how Phase 3 fitted the curve

Phase 3 regressed the 2026 file's `Punkte` on its `Marktwert`, i.e. **2025/26 points on 2026/27
prices**. That estimates `E[last season's points | this season's price]`, not the
`E[points in season S | price in season S]` the projection needs. The two differ precisely because
the price already absorbs last season plus transfers and ageing — the mismatch is not a small bias,
it is a different estimand. The 2023-2025 files supply the correct pairing directly, so the curve
must be refitted on those and the 2026 file demoted to a feature source. Every number quoted in
Phase 3 below (slope 53.8, the intercepts, the break-even values) is provisional until that refit.

### `999000000` is a "not purchasable" sentinel

60 rows in the 2024 file and 12 in the 2025 file carry a market value of **999,000,000**, all with
essentially zero points; the 2023 and 2026 files have none. It marks a player who cannot be bought
(unregistered, long-term injured, or arrived after pricing). Current validation only rejects
non-positive values, so these pass straight through, and they are not cosmetic:

- a 999M player with 0 points is a colossal leverage point that drags the fitted slope toward zero;
- it silently corrupts **within-club price rank**, which the goalkeeper model below depends on — it
  is why the top-priced keeper in 2024/25 appears to have a mean market value of 501M.

The loader must reject the sentinel explicitly rather than clamping or winsorising it, and the
affected players must drop out of both fit and pool: they were genuinely unavailable that season.

### The filename convention now has two forms

`2023_spieler_daten.csv` does not match the existing `YYYY_MM_DD` pattern, so the loader currently
rejects the three historical files outright. It also needs to stop assuming one export: `latest_export`
still gives the pool to *pick from*, but fitting now consumes every season file.

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

### What the panel now makes measurable

Three seasons linked by a stable `ID` turn two parameters that were previously guesses into
estimable quantities. This is the single largest gain from the new data:

- **`residual_weight` becomes identifiable.** It was set to 0.5 by judgement because one export
  cannot separate persistent skill from one season of luck. With a panel, regress a player's
  season-`S+1` residual on his season-`S` residual: the slope *is* the weight, estimated rather than
  assumed. Two transitions are available (2023/24→2024/25, 2024/25→2025/26). Given it currently
  swings 5 of the top 11 players, this materially changes the recommended squad.
- **The club effect stops being ambiguous.** Phase 3 left it out because one season cannot
  distinguish a persistent squad-rotation effect from last season's over-performance being priced
  out. The same year-over-year regression at club level settles the sign directly. If it is
  persistent it belongs in the curve — worth up to ±25 points per player.

Both are ordinary auto-regressions on residuals, not new machinery, and neither needs scraping.

**Sequencing consequence:** appearances still require scraping (Phase 4), but the panel makes a
genuine mid-step possible without it — a correctly specified multi-season curve with an *estimated*
shrinkage weight. Phases 1-3 and 5 do not block on scraping.

## Architecture

Flat module set under `src/kicker_manager_analysis/` — no subpackages, per the simplicity rule in
`AGENTS.md`:

| Module | Responsibility |
|---|---|
| `config.py` | `pydantic-settings` `Settings`: budget, squad/formation quotas, club cap, `bench_weight`, data dir |
| `data.py` | Polars load of one export or the whole multi-season panel, schema validation, sentinel rejection, canonical `Player` frame; optional appearance join |
| `scoring.py` | kicker scoring rules as pure functions (grade→points, goal points by position) — used to sanity-check `Punkte` and to decompose it |
| `projection.py` | expected season points per player: the outfield market curve, the separate goalkeeper model, and the panel-estimated shrinkage |
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

**Phase 3 — Baseline projection (CSV only).** *Done — `projection.py`, `notebooks/projection.py`.*
Market value is itself the editorial forecast of a player's season, so `Punkte` is regressed on
`Marktwert` and position to recover the market's implied points curve; the residual *is* the edge
the optimizer exploits. Players with no Bundesliga history fall back to the market-value-only prior
rather than an implicit zero. What the fit established:

- **Linear in market value, not logarithmic** (cv R² 0.67 against 0.51 for log). One shared slope of
  **53.8 points per million** with per-position intercepts; per-position *slopes* add nothing out of
  sample and the 24 goalkeepers with history cannot support one.
- **The intercepts are negative**, so points per euro *rises* with price: an outfield player at 500k
  projects to nothing, while 3M returns 40-45 points per million. Break-even is 0.29M for a
  goalkeeper, 0.70M/0.71M for a defender/midfielder, 1.01M for a forward. This independently
  justifies the cheapest-possible bench — cheap players are poor value even when they do play.
- **The fit sample is the subtle part.** A zero must be kept when the player was in the league and
  did not feature (informative) and dropped when his club was not in the league (missing). Dropping
  both flips the goalkeeper intercept from −15.8 to **+11.6** and makes a 500k keeper look like the
  best value in the pool at 76 points per million against a true 22 — the optimizer would have
  bought that keeper every time. Promoted clubs are identified from the share of each squad with
  history (3-6% against 57%+ elsewhere), never by name, so this survives a new season.
- **`residual_weight` (default 0.5) is not identifiable from one export.** It blends observation into
  prior: `projected = curve + w·(observed − curve)`. Only 6 of the top 11 players survive the move
  from w=0 to w=1, so it is the model's most consequential free parameter. Phase 4's shrinkage
  replaces it.

Deliberately **not** included: a club effect. Club dummies lift R² from 0.75 to 0.80 with Bayern at
−25 and Hoffenheim at +25 points, but one season cannot distinguish a persistent squad-rotation
effect (deep squads share minutes, so buy Hoffenheim) from last season's over-performance being
priced out (so avoid it). The two readings invert the recommendation, so the effect stays out until a
second season resolves it. *The panel now resolves it — see Phase 3b.*

**Phase 3b — Refit on the panel, and split goalkeepers out.** *Supersedes the single-season fit
above.* Three changes, in dependency order.

**(a) Refit the outfield curve on properly paired seasons.** Stack the 2023/24, 2024/25 and 2025/26
files and fit `points(S) ~ market value(S) + position`, pooled across seasons with a season intercept
to absorb league-wide scoring drift (season point totals run 36.2k / 32.4k / 32.6k, so the drift is
real). Then estimate `residual_weight` and the club effect from the year-over-year residual
regressions described above rather than assuming them.

**(b) Model goalkeepers separately, on within-club price rank.** Goalkeeping is not a line on price,
it is a step function on *who plays*, because keepers are almost never substituted — a club's number
one plays ~34 matches and his deputy plays ~0. Mean points by within-club price rank:

| season | rank 1 | rank 2 | rank 3 |
|---|---|---|---|
| 2023/24 | **179.1** | 17.6 | 13.2 |
| 2025/26 | **156.9** | 45.4 | 4.7 |

(2024/25 omitted: its ranks are corrupted by the 999M sentinel and must be recomputed after the
loader filters it.)

The gap is an order of magnitude, and it is **rank** that carries it, not absolute price. This
explains the Phase 3 goalkeeper anomaly properly: fitting GK points on absolute market value
produced a positive intercept and made a 500k keeper look like the pool's best value at 76 points
per million. The real structure is that a 500k keeper is almost always somebody's number two and
scores nothing, while rank alone separates 160 points from 20.

Consequences for the model:

- The goalkeeper projection becomes `P(is the club's number one) × expected points of a number one`,
  with the rank and the price gap to the next keeper at the same club as features. The 15-man squad
  needs 2 keepers but only 1 scores, so the second should be the cheapest legal body — the optimizer
  already does this at `bench_weight` 0, but it now has a *reason* rather than a coincidence.
- The optimizer should be discouraged from buying a club's number two at any price. Worth checking
  whether this needs an explicit constraint or falls out of the projection.

One caveat on the stated mechanism. The intuition was that a *clear price gap* to the next keeper
signals a settled number one, and a narrow gap signals a contest. Measured on the sentinel-corrupted
data, 47 of 53 club-seasons already show a gap of 2x or more, so the gap rarely discriminates and the
six "narrow gap" cases actually scored *higher* (171 vs 137) — but n=6 and the ranks were corrupted,
so this is not yet a real result. **Recompute after (a) and (b) land**; if the gap turns out not to
matter, rank alone is the model and the practical rule is simply "buy a club's number one, never his
deputy".

**(c) Fix the zero-point treatment for new signings.** Deferred earlier by choice; the panel now
resolves most of it without scraping. A player's presence in the previous season's file *is* the
per-player league-registration flag that was missing, so the three-way split becomes available
directly: played / was registered but did not play / was not in the league. That removes the
inconsistency where a newcomer at a promoted club got the full market prior while a newcomer at an
established club was penalised as a known non-player.

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
- **The 999M sentinel must fail loudly**, in the loader and on every historical file — 60 rows in
  2024 and 12 in 2025 that currently pass validation unnoticed. Assert no surviving market value
  exceeds a sane ceiling, and assert the goalkeeper price ranks recomputed after filtering no longer
  show a club-season with a mean top-keeper value in the hundreds of millions.
- **Out-of-sample validation is now possible and should be the headline check.** Fit on 2023/24 and
  2024/25, predict 2025/26, and compare against the two baselines that matter: predicting from market
  value alone, and predicting last season's points forward. A model that does not beat both is not
  earning its complexity. This replaces the in-sample cross-validation Phase 3 relied on.
- **Goalkeeper model:** assert that within-club price rank 1 predicts the season's top-scoring keeper
  at that club in a clear majority of club-seasons, and that the rank-1/rank-2 points gap survives
  sentinel filtering. If it does not, the step-function model is wrong and should be dropped.
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
- ~~Which season `Punkte` / `Notendurchschnitt` describe~~ — resolved by the multi-season files: the
  club list is the upcoming season's squads, the points are the completed season, and the 2026 file
  repeats the 2025 file's results against new prices. See the data table above.
- ~~Whether the club effect is persistent or mean-reverting~~ — no longer an open question in
  principle, just work: the panel answers it by regressing club residuals year over year (Phase 3b).
- Whether kicker.de player pages expose appearances in a stable, scrapeable form; if not, Phase 4 falls
  back to openligadb lineups.
- **Why the 2024 file holds only 16 clubs** where the Bundesliga has 18, and whether that export is
  otherwise complete. It also carries by far the most sentinel rows (60). Until this is understood,
  treat 2024/25 as the least trustworthy of the three seasons and check whether conclusions hold
  without it.
- **Goalkeeper availability beyond price rank.** Rank is measured *within the export*, so it tells us
  who kicker priced as the number one, not who the coach picks after a summer signing. ligainsider's
  expected-starter signal remains the direct observation; the rank model is a strong proxy for it.
- **How much of the 2026 pool is genuinely new.** 549 players against 477 the season before, and 224
  zero-point rows. The three-way registration split in Phase 3b(c) should be checked against the
  previous file rather than assumed.
