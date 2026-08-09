# Results — appearance data, and a goalkeeper model

*Iteration summary. Covers what changed, what it bought us, and what we tested and rejected.*

## Where we were

The task is to pick a 15-man kicker-Managerspiel squad for 30M, of which only the fixed 4-4-2 XI
scores. The binding constraint is budget, so the estimand is expected season points per euro, not
talent.

The previous iteration fitted a market curve — season points on market value, one shared slope with
per-position intercepts — on a three-season panel, and reached an uncomfortable conclusion: **after
conditioning on price, a player's residual carries essentially nothing into the next season**
(+0.03 pooled, and independently ~0 for every outfield position). Market value already contains
what last season had to say. Goalkeepers were the lone exception at +0.45, which we suspected was
the number-one/number-two step function rather than anything about keeping ability, but we could not
show it: the export has season point *totals* and no appearance counts, and the totals cannot be
inverted for one (grade points pass through zero at a grade of 4.5, so the total is uninformative
about how often a player featured).

That left the projection nearly degenerate for outfield players and the goalkeeper story
unfalsifiable.

## What is new

Per-season JSON payloads from the game's own API, for 2023/24, 2024/25 and 2025/26. They reconcile
with the CSV exports exactly — same row counts, and `marketValue` / `rating` / `averageGrade`
byte-equal to `Marktwert` / `Punkte` / `Notendurchschnitt` for every shared id. Same data universe,
so nothing had to be aligned or joined on fuzzy keys.

What they add is `ratingBreakDown`, which decomposes each season total into its scoring channels and
its **counts**, including `starter` and `joker` — matches started and matches come on as a
substitute. Appearances are their sum. The decomposition closes exactly on all 1452 rows (the eight
channels sum to the total), which we now enforce in the loader rather than test after the fact.

Panel after filtering the "not purchasable" 999M sentinel: **1380 player-seasons**. Pool to pick
from: 549.

## Result 1 — the persistence result was a composition artefact

Decomposing the same residual — points = appearances × points-per-appearance, each residualised on
the same price curve — separates two factors that behave nothing alike:

| position | n | residual **points** | residual **appearances** | residual **rate** |
|---|---|---|---|---|
| GOALKEEPER | 70 | +0.450 | **+0.573** | +0.163 |
| DEFENDER | 190 | −0.083 | **+0.168** | −0.039 |
| MIDFIELDER | 183 | +0.028 | **+0.349** | −0.028 |
| FORWARD | 92 | −0.077 | **+0.445** | +0.011 |

"No edge" is really **no edge in rate**. How well a player performs per appearance is, after price,
unpredictable everywhere — keepers included, at +0.16. How *often* he plays is persistently
predictable beyond price (+0.31 outfield pooled; stable at +0.29/+0.32 restricting to players with
5 or 10 appearances in both seasons). Out of sample, adding prior appearances lifts the prediction of
next season's appearances from R² 0.32 to **0.41**.

It also reinterprets the goalkeeper number: +0.45 was never about keeping ability, it was the
appearance channel at +0.57.

## Result 2 — the goalkeeper model, which ships

Keeping is a step function on who plays, not a line on price — keepers are barely ever substituted.
With appearances we can measure the step instead of inferring it. Ranking each keeper against **his
own club's keepers** by price (ranking against the whole squad puts him eighth and destroys the
signal):

| within-club price rank | n | mean appearances | mean points |
|---|---|---|---|
| 1 | 49 | **27.9** | **183.2** |
| 2 | 49 | 4.0 | 24.7 |
| 3 | 48 | 1.7 | 11.8 |

So the projection for keepers becomes `P(number one | rank) × points of a number one`, fitted as:

| quantity | value |
|---|---|
| P(≥17 appearances \| rank 1) | 0.88 (43/49) |
| P(≥17 appearances \| rank 2) | 0.08 (4/49) |
| P(≥17 appearances \| rank 3+) | 0.03 (2/70) |
| points of a number one | 174.5 + 6.8 × M |
| points of a deputy | 11.9 |

**The price term is the actionable finding: it is economically nothing.** The first-choice branch
slopes at +6.8 points per million against a ~175-point intercept; restricted to the 49 rank-1
keepers the slope is *negative*, −3.0/M, correlation −0.03. The cheapest number one in each season
scored 257, 261 and 230 — the dearest scored 208, 213 and 254.

The rule is therefore **buy the cheapest keeper who is clearly his club's number one**, not the best
keeper. In the current pool that is a 2.4M keeper at ~70 projected points per million against ~53
for the 3.6M alternative. Every one of the top eight points-per-million players in the whole pool is
now a rank-1 keeper.

Fitting the step explicitly absorbs about half the goalkeeper residual persistence, +0.45 → +0.24,
confirming the old blend weight was standing in for this model. The remainder stays as a measured
weight (outfield weights remain ~0).

Validation: across 49 club-seasons the highest-priced keeper is both the most-appearing and the
top-scoring keeper at his club in **44 (90%)**. Asserted in the test suite, with instructions to drop
the model rather than tune it if that regresses toward chance.

## Result 3 — out-of-sample scoring, cross-fitted

We now hold each season with a predecessor out in turn, refit the whole model from scratch on the
rest, and score against the two baselines that matter. Both are strong, and a model that does not
beat both is not earning its complexity.

RMSE on held-out season points:

| held out | n | projection | market value alone | last season's points |
|---|---|---|---|---|
| 2024/25 | 280 | **50.5** | 50.9 | 57.8 |
| 2025/26 | 255 | **53.4** | 55.4 | 64.7 |
| mean R² | | **0.477** | 0.453 | 0.273 |

The projection wins in *both* seasons, which is the assertion in the test — with only two held-out
seasons an average can hide a reversal. The margin over price alone is thin and comes almost entirely
from the goalkeeper model. That is the honest reading: for outfield players, the projection
essentially *is* the price.

## Result 4 — what we tested and did not ship

The obvious next step was the decomposition `E[points] = E[appearances] × E[rate]`, given that
appearances are the predictable factor. We cross-fitted it over both predictable seasons, with the
rate shrunk fully to its price prediction, and evaluated on a decision-relevant loss as well as RMSE:
the *realised* points of the XI a 30M/3-per-club ILP picks from each projection.

| model | RMSE | R² | XI points 24/25 | XI points 25/26 |
|---|---|---|---|---|
| curve only (previous iteration) | 51.4 | +0.472 | 1025 | 825 |
| **curve + GK rank model** | **50.1** | **+0.498** | **1262** | **938** |
| decomposed E[app] × E[rate] | 53.3 | +0.433 | 973 | 1355 |
| decomposed + GK rank model | 51.5 | +0.471 | 1093 | 1170 |

(Perfect foresight scores 2448 and 2672, so everything here is well short of the ceiling.)

The goalkeeper model wins on every metric in both seasons — clear enough to ship. **The decomposition
does not.** It is better on the XI metric on average but worse in one of the two seasons and worse on
RMSE; with n=2 that is noise, not evidence. Availability is genuinely more predictable than points,
but the predictability does not survive multiplication by a rate whose residual is zero — the rate
variance swamps the improved availability estimate.

It stays out until there is enough data to adjudicate. Three things worth trying, cheapest last:

1. **Availability as a constraint, not a term** — forbid the optimizer from fielding a player whose
   predicted appearances fall below a floor, using the signal for ranking who plays without letting
   it multiply into the points estimate.
2. **Empirical-Bayes shrinkage per player**, weighted by each player's appearance count, rather than
   the all-or-nothing shrink tested here.
3. **Wait for 2026/27** — a third transition roughly halves the standard error on every persistence
   estimate above, and costs nothing.

## Also delivered

- **Registration split.** A zero in the points column means opposite things depending on why it is
  there, and only membership of the previous season's frame plus its appearance count separates them.
  Every pool player is now labelled `PLAYED` (309), `REGISTERED` — in the league, never featured, which
  counts against him (37), or `ABSENT` — not in the league, left on the model's prior rather than
  penalised with an implicit zero (203).
- **Two latent bugs**, both surfaced by the backtest harness: the curve fit crashed on a single-season
  panel, and the persistence estimator stored NaN weights when residuals had zero variance.
- **Reduced scraping scope.** kicker.de and openligadb are no longer needed; the payloads *are* the
  kicker data. The only remaining case is a forward-looking availability signal for the 146 of 549
  pool players (27%) with no appearance history — mostly cheap, only 16 above 2M.

## Caveats a reviewer should hold onto

- **Two held-out seasons.** Every out-of-sample number above rests on n=2 seasons. It is enough to
  reject the decomposition as unproven and to accept the goalkeeper model as consistent, and not much
  more.
- **2024/25 is missing two clubs.** Its payload lists all 18 teams and 306 matches but carries zero
  player rows for Bochum and Holstein Kiel, both relegated after that season — the export was taken
  after their squads were dropped. That season is therefore a slightly stronger league than reality.
  It is a selection bias, not a data-quality one, and conclusions should be re-checked without it.
- **The outfield projection is still close to degenerate.** With residual weights at ~0, the optimizer
  will be nearly indifferent between ways of spending the same money, its answer decided by small
  intercept differences. The squad it recommends will not be trustworthy on the outfield side until
  something breaks that tie.
- **Rank is measured in the export**, so it tells us who kicker priced as the number one, not who the
  coach picks after a summer signing. It is a strong proxy at 90%, not an observation.

## Reproducing

```bash
uv run python -c "
from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.data import load_panel
from kicker_manager_analysis.backtest import backtest, backtest_summary
s = Settings(); print(backtest_summary(backtest(load_panel(s.data_dir), s)))"
```

Model fitting lives in `src/kicker_manager_analysis/projection.py` (`fit_model`,
`GoalkeeperModel`), validation in `backtest.py`, and the full phase-by-phase record in
`doc/plan.md`.
