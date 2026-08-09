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

### The JSON exports carry appearances and the full points decomposition

`data/json/{2023,2024,2025}.json` are the game's own API payloads for the same three seasons, keyed by
the same stable `ID`. They reconcile against the CSVs exactly — same row counts (488/487/477), and for
every player `marketValue`, `rating` and `averageGrade/100` are byte-equal to `Marktwert`, `Punkte` and
`Notendurchschnitt`. **This is the same data universe, not a second source to be aligned.**

What they add is `ratingBreakDown`, which decomposes each player's season total into its scoring
components and, critically, its **counts**:

| field | meaning |
|---|---|
| `starter` / `joker` | matches started / came on as a substitute — **appearances = their sum** |
| `goals`, `assists`, `mvp`, `cleanSheet` | event counts |
| `cardsYellowRed`, `cardsRed` | disciplinary counts |
| `averageGrade` | mean kicker grade ×100 (`0` still means never graded) |
| `rating*` | each count already converted to points |

The decomposition is exact: `ratingSum` equals the sum of its eight `rating*` components and equals
`rating` (= `Punkte`) for all 1452 rows across the three files, with no exceptions. Appearances are
bounded by the 34-match season in every row, and no player has points without appearances or
appearances without points.

Note that `ratingGrade` is computed over *graded* appearances, which is fewer than `starter + joker` —
short substitute cameos go ungraded. This is why Phase 2's attempt to invert the points formula for an
appearance count failed on top of the singularity at grade 4.5: it was solving for the wrong count. The
JSON supplies the real one directly, so the inversion is moot.

The files also carry `teams`, `rounds` (34) and `matches` (306), but the matches are **fixtures only** —
ids, teams and kickoff dates, no lineups or results. Per-matchday appearance data is therefore *not*
in the JSON; `data/additional_match_data/2425_player_raw_match_data.csv` remains the only per-match
source, and only for 2024/25.

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
(unregistered, long-term injured, or arrived after pricing). The appearance data confirms this
reading rather than inferring it from points: **71 of the 72 sentinel rows have zero appearances**,
and the one exception is a single substitute appearance worth 2 points. They did not play.
Current validation only rejects non-positive values, so these pass straight through, and they are
not cosmetic:

- a 999M player with 0 points is a colossal leverage point that drags the fitted slope toward zero;
- it silently corrupts **within-club price rank**, which the goalkeeper model below depends on — it
  is why the top-priced keeper in 2024/25 appears to have a mean market value of 501M.

The loader must reject the sentinel explicitly rather than clamping or winsorising it, and the
affected players must drop out of both fit and pool: they were genuinely unavailable that season.

### The filename convention now has two forms

`2023_spieler_daten.csv` does not match the `YYYY_MM_DD` pattern, so the loader accepts both — the
leading year is the season key either way. *Resolved:* `latest_export` gives the pool to pick from,
while fitting reads the JSON payloads instead, keyed by their own `YYYY.json` names. The CSV panel
loader and its repricing detector are gone; with a payload per completed season there is no
mismatched pairing left to detect.

**The denominator is no longer missing** — the JSON supplies it for all three seasons, so
`Punkte ÷ Einsätze` is directly computable and Phase 4 no longer blocks on scraping. What remains
missing is appearances for the **2026/27 pool itself**, which is unknowable in advance and is exactly
what the projection has to estimate. Two further gaps:

- **224 of 549 players (41%) have 0 points**, and Phase 2 EDA shows the cohort is dominated by the
  three promoted clubs — Paderborn 97%, Elversberg 96%, Schalke 94% — which contribute 89 of the 224.
  Those players have **no** Bundesliga history rather than a bad one. The rest scatter at 11-43%
  across established clubs (fringe players, new signings). A zero here does not mean "bad", and the
  cohort reaches 4.5M in market value, so it cannot be discarded.
- `Notendurchschnitt` is confirmed (Phase 2 EDA) as the mean kicker grade: non-zero values span
  1.5-5.0 with mean 3.57. **`0.0` is a sentinel for "never graded", not a grade** — 244 players carry
  it, and feeding it to the scoring formula would imply `(3.5 − 0) × 4 = +14` points per appearance,
  better than a perfect 1.0. The projection must mask it rather than treat the column as numeric.

The scraping plan is therefore reduced, not cancelled. kicker.de player pages and openligadb are no
longer needed — the JSON *is* the kicker data, already reconciled. What no historical file can supply
is a forward-looking availability signal for the pool we actually buy from: **146 of the 549 players
in the 2026/27 pool (27%) have no appearance history in any of the three seasons**, median value 1.4M
and 16 of them above 2M. For those, ligainsider's expected-starter and injury signals remain the only
observation, and that is now the sole scraping case. Any such pull gets cached to `data/` under the
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
| `data.py` | Polars load of the CSV pool and the JSON season panel, schema validation, sentinel rejection, canonical `Player` frame with appearances and event counts |
| `scoring.py` | kicker scoring rules as pure functions (grade→points, goal points by position) — used to sanity-check `Punkte` and to decompose it |
| `projection.py` | expected season points per player: the outfield market curve, the separate goalkeeper model, and the panel-estimated shrinkage |
| `backtest.py` | hold one season out, refit, and score the projection against the two baselines |
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

**(a) Refit the outfield curve on properly paired seasons.** *Done.* Results:

- **The corrected fit is much weaker and that is the honest number.** R² falls to **0.43** from
  0.67 on the same specification (0.75 for the original Phase 3 fit, flattered further by an
  exclusion the corrected pairing makes unnecessary), and the slope from 53.4 to **38.8** points
  per million. The old fit regressed a season's points on the *next* season's prices, which
  already knew the outcome; predicting an unrealised season is harder than describing a finished
  one.
- **"Points per euro rises with price" is retracted.** It followed from intercepts of −15.8 to
  −54.4, which the mismatched pairing inflated. On matched seasons they are +9.7 / −1.5 / −3.4 /
  −11.1 (GK/DEF/MID/FWD), so efficiency is close to **flat** and cheap players are not
  systematically poor value. What survives is that forwards convert price to points worst.
- **The cold-start exclusion disappeared.** Excluding promoted clubs was an artefact of the bad
  pairing: on matched seasons their players did play the season being measured, so every row is a
  valid observation. `new_clubs` and `new_club_threshold` are gone.
- **Season effects are negligible** (−0.8 / −1.5 / +2.3 points, centred), but kept so an unseen
  season prices off an average rather than off whichever season happened to be the base.
- **`residual_weight` is now measured, not chosen** — see below. It survives in `Settings` only as
  an override.

**The headline result: there is no outfield stock-picking edge in this data.** Raw points persist
year over year at +0.59, but the *residual* — what is left after the price — persists at **−0.04**,
and every outfield position and price band lands on zero independently:

| position | transitions | residual correlation S→S+1 | weight used |
|---|---|---|---|
| GOALKEEPER | 70 | **+0.450** | 0.418 |
| DEFENDER | 190 | −0.083 | 0 |
| MIDFIELDER | 183 | +0.028 | 0.024 |
| FORWARD | 92 | −0.077 | 0 |

For outfield players the market value already contains everything last season had to say, so the
projection reduces to the curve. Goalkeepers are the sole exception, at +0.45 — precisely the
step-function structure of being first choice, and the reason (b) exists. Negative estimates are
clamped to zero: at these correlations they are noise, not exploitable mean reversion.

**The appearance data narrows that claim.** Decomposing the same residual — points = appearances ×
points per appearance, each residualised on the same price curve — shows the two factors behave
completely differently:

| position | n | residual **points** | residual **appearances** | residual **rate** |
|---|---|---|---|---|
| GOALKEEPER | 70 | +0.450 | **+0.573** | +0.163 |
| DEFENDER | 190 | −0.083 | **+0.168** | −0.039 |
| MIDFIELDER | 183 | +0.028 | **+0.349** | −0.028 |
| FORWARD | 92 | −0.077 | **+0.445** | +0.011 |

So "no edge" is really **no edge *in rate***. How well a player performs per appearance is, after
price, unpredictable at every position — the rate residual is zero everywhere, goalkeepers included.
But **how often he plays is persistently predictable beyond his price** (+0.31 for outfield players
pooled, stable at +0.29/+0.32 when restricted to players with 5 or 10 appearances in both seasons).
Out of sample, prior appearances lift the prediction of next season's appearances from R² 0.32
(price alone) to **0.41**.

This also reinterprets the goalkeeper result. The +0.45 points residual is *not* keepers being more
readable as players — their rate residual is +0.163, no better than anyone's. It is entirely the
appearance channel at +0.573, i.e. the number-one/number-two step function that (b) models.

**The club effect is resolved and stays out.** Across 31 club transitions the year-over-year slope
is **−0.164** (correlation −0.224) — *mean-reverting*. That is the repricing reading, not the
squad-depth one: a club whose players beat their prices tends to fall below them next season. The
effect is not a persistent edge, so it does not enter the projection, now on evidence rather than
caution.

**Consequence for Phase 5.** With outfield weights at zero the outfield choice is close to
degenerate — the solver will be nearly indifferent between two ways of spending the same money and
its answer decided by small position-intercept differences. The optimizer is still worth building,
but the squad it recommends will not be trustworthy until either the goalkeeper model (b) or
appearances (Phase 4) break the tie.

**(b) Model goalkeepers separately, on within-club price rank.** *Done — `GoalkeeperModel` in
`projection.py`.* Goalkeeping is not a line on price,
it is a step function on *who plays*, because keepers are almost never substituted — a club's number
one plays ~34 matches and his deputy plays ~0. Mean points by within-club price rank:

Recomputed on all three seasons with the sentinel filtered out (2024/25 is no longer omitted, and the
appearance columns are the direct evidence for the step function rather than a proxy for it):

| season | rank 1 pts | rank 2 | rank 3 | rank 1 **apps** | rank 2 | rank 3 |
|---|---|---|---|---|---|---|
| 2023/24 | **179.1** | 19.6 | 6.0 | **27.3** | 3.2 | 1.0 |
| 2024/25 | **185.1** | 36.8 | 12.3 | **27.7** | 5.9 | 1.9 |
| 2025/26 | **186.1** | 17.8 | 18.2 | **28.9** | 3.1 | 2.5 |

The mechanism is now visible rather than inferred: the number one plays ~28 of 34 matches and the
deputy plays ~3-6. **The plan's verification criterion passes decisively** — across 49 club-seasons,
the highest-priced keeper is both the most-appearing and the top-scoring keeper at his club in
**44 of 49 (90%)**. Rank-1 keepers fall below 17 appearances in only 6 of 49 club-seasons, so the
downside case is rare but real and is what the projection's `P(number one)` term should price.

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

As implemented, the model is `P(number one | rank) x (points of a number one)` with the deputy
branch carrying the small remainder. Fitted on all three seasons it gives:

| quantity | value |
|---|---|
| P(≥17 appearances \| rank 1) | **0.88** (43 of 49) |
| P(≥17 appearances \| rank 2) | 0.08 (4 of 49) |
| P(≥17 appearances \| rank 3+) | 0.03 (2 of 70) |
| points of a number one | 174.5 + 6.8 × M |
| points of a deputy | 11.9 |

**The price term is the striking part: it is nothing.** Across the 49 number ones in the panel,
points correlate with price at **−0.03**. The cheapest number one of each season scored 257, 261
and 230; the dearest scored 208, 213 and 254. So the practical rule is not "buy a good keeper" but
**buy the cheapest keeper who is clearly his club's number one** — the rank buys the points and the
extra millions buy nothing. On the 2026/27 pool that is a 2.4M keeper at ~70 points per million
against ~53 for the 3.6M alternative.

The rank-gap idea is dropped. The intuition was that a clear price gap to the next keeper signals a
settled number one; recomputed on sentinel-filtered ranks the probabilities above already separate
rank 1 from rank 2 by an order of magnitude, so the gap has nothing left to explain. Rank alone is
the model.

Fitting the step explicitly also **absorbs half the goalkeeper residual persistence**, which falls
from +0.45 to +0.24 once residuals are taken against the step model rather than the curve. That is
the sense in which the old blend weight was a proxy for this model; the remaining +0.24 stays as a
measured weight.

**(c) Fix the zero-point treatment for new signings.** *Done — `Registration` in `projection.py`.*
A player's presence in the previous season's file *is* the per-player league-registration flag that
was missing, and the appearance count splits that presence in two, so the projection now labels
every player in the pool as one of:

- `PLAYED` — in the league and featured, so his residual is a real observation (309 of the pool);
- `REGISTERED` — in the league and never featured, which counts against him, because he was
  available and was not picked (37);
- `ABSENT` — not in the league at all, left on the model's prior rather than penalised with an
  implicit zero he did not earn (203).

That removes the inconsistency where a newcomer at a promoted club got the full market prior while a
newcomer at an established club was penalised as a known non-player.

**Phase 4 — Per-appearance refinement.** *Unblocked by the JSON, but the naive form does not yet pay.*
The intended decomposition is:

```
E[season points] = E[appearances] × E[points per appearance]
```

with points-per-appearance estimated from the prior season and **shrunk toward the position mean** in
proportion to sample size (empirical-Bayes / James–Stein), so that a player with three lucky matches
does not outrank a proven regular — precisely the noise-driven cases the optimizer hunts for.

**Tested and not shipped: the decomposition does not reliably beat the curve.** Cross-fitted over both
seasons that can be predicted at all — each held out in turn, the model refitted on the other two, and
the rate shrunk all the way to its price prediction as suggested below:

| model | RMSE | R² | XI points, 24/25 | XI points, 25/26 |
|---|---|---|---|---|
| curve only (Phase 3b) | 51.4 | +0.472 | 1025 | 825 |
| **curve + GK rank model** | **50.1** | **+0.498** | **1262** | **938** |
| decomposed `E[app] × E[rate]` | 53.3 | +0.433 | 973 | 1355 |
| decomposed + GK rank model | 51.5 | +0.471 | 1093 | 1170 |

"XI points" is the decision-relevant loss suggested below — the *realised* points of the XI a
30M/3-per-club optimizer picks from each projection, against 2448 and 2672 for perfect foresight.

Reading it honestly: **the goalkeeper model is a clear win and ships** — better on RMSE, on R², and on
the squad metric in both seasons. **The decomposition is not** — better on the squad metric on average
but worse in one of the two seasons and worse on RMSE, which with n=2 is noise rather than evidence.
Availability is genuinely more predictable than points (prior appearances lift appearance R² from 0.32
to 0.41), but that predictability still does not survive multiplication by a rate whose residual is
zero. It stays out of the projection until a fourth season can adjudicate.

What remains worth trying, in order of expected value:

1. **Availability as a constraint, not a term.** Rather than scaling the projection, forbid the
   optimizer from fielding a player whose predicted appearances fall below a floor. That uses the
   signal where it is strong (ranking who plays) without letting it multiply into the points estimate.
2. **Shrink per player, not per position.** The rate was shrunk fully to its price prediction here;
   an empirical-Bayes weight that varies with each player's appearance count is the version the phase
   originally proposed and has not been tried.
3. **Wait for 2026/27.** A third transition roughly halves the standard error on every persistence
   estimate above, and is free.

Availability carries extra weight here because a zero-weighted bench means a missed match is simply
points forgone. It is also where the 27% of the 2026/27 pool with no history is most exposed, and where
ligainsider's expected-starter and injury signals would enter.

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
- **Out-of-sample validation is the headline check.** *Done — `backtest.py`, asserted in
  `tests/test_backtest.py`.* Each season with a predecessor is held out in turn, the model refitted
  from scratch on the rest, and scored against the two baselines that matter. The model must beat both
  in **every** held-out season rather than on average — with only two of them, an average can hide a
  reversal:

  | held out | projection | market value alone | last season's points |
  |---|---|---|---|
  | 2024/25 | **50.5** | 50.9 | 57.8 |
  | 2025/26 | **53.4** | 55.4 | 64.7 |

  (RMSE over the 280 and 255 players with a previous season.) The margin over the price alone is thin
  and comes almost entirely from the goalkeeper model — which is the honest reading of Phase 3b: for
  outfield players the projection essentially *is* the price.
- **JSON/CSV reconciliation is a cheap, strong invariant** — *done, `test_real_panel_reconciles_with_
  the_csv_exports`.* For every shared `ID`, `marketValue`, `rating` and `averageGrade/100` must equal
  `Marktwert`, `Punkte` and `Notendurchschnitt`. It holds exactly today, so any future drift means one
  of the two sources was re-exported for a different season.
- **The points decomposition must close** — *done, enforced in the loader itself rather than a test:*
  `ratingSum` equals the sum of its eight `rating*` components and equals `rating`, and appearances
  never exceed the payload's own round count. A violation means the breakdown and the total describe
  different things, and the appearance counts drawn from it could not be trusted.
- **Goalkeeper model** — *done, `test_real_panel_number_one_keepers_dominate`.* Within-club price rank
  1 must be both the most-appearing and the top-scoring keeper at his club in a clear majority of
  club-seasons. Measured at **44 of 49 (90%)** after sentinel filtering; the test asserts above 80%,
  and that rank 1 averages over 25 appearances against under 8 for rank 2. If this regresses toward
  chance the step-function model is wrong and should be dropped rather than tuned.
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
- ~~Whether kicker.de player pages expose appearances in a stable, scrapeable form~~ — moot. The JSON
  exports carry appearances for all three seasons directly; no scraping is needed for history.
- ~~**Why the 2024 file holds only 16 clubs**~~ — resolved by the JSON. That file's `teams` list holds
  all **18** clubs and its 306 `matches` reference all 18, but **VfL Bochum and Holstein Kiel have zero
  player rows**. Both were relegated after 2024/25, so the export was taken once their squads had been
  dropped from the game. The file is otherwise complete — indeed its 16 clubs carry *fuller* rosters
  than the other seasons (30.4 players per club against 27.1 and 26.5). The consequence is a
  **selection bias, not a data-quality one**: 2024/25 is missing the players of exactly the two worst
  clubs, so any statistic pooled over that season is computed on a slightly stronger league. Worth
  re-checking the panel conclusions with 2024/25 dropped.
- **Goalkeeper availability beyond price rank.** Rank is measured *within the export*, so it tells us
  who kicker priced as the number one, not who the coach picks after a summer signing. The rank model
  is now measured at 90% accuracy on history (above), but that is the in-league case; ligainsider's
  expected-starter signal remains the direct observation for a keeper new to the pool.
- ~~**How much of the 2026 pool is genuinely new**~~ — measured against the JSON history: of 549
  players, **356 appear in 2025/26, 403 in at least one of the three seasons, and 146 (27%) in none**.
  The newcomers split 53 DEF / 49 MID / 33 FWD / 11 GK, median value 1.4M, and only 16 exceed 2M — so
  the cold-start cohort is real but concentrated in cheap players the optimizer has little reason to
  buy. The three-way registration split in Phase 3b(c) is now directly constructible: appearances > 0
  (played), appearances = 0 while present in that season's file (registered, did not play), absent
  from the file (not in the league).
