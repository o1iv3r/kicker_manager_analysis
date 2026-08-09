# TODO

What is left after the appearance-data iteration (see `doc/results.md`). The projection is done and
validated; **nothing downstream of it exists yet** — there is no optimizer, no report, and no CLI, so
the repo cannot currently answer its own question.

Ordered by what blocks what.

---

## 0. Exclude a player and re-run — **done**

*Motivation: news breaks (an injury, a transfer, a suspension) that makes a player unbuyable or
unattractive, and the answer has to be recomputed in seconds without editing code or data.*

Implemented as `Settings.excluded_players` plus `data.apply_exclusions`, applied inside
`load_latest_players`. No code change is needed to use it:

```bash
KICKER_EXCLUDED_PLAYERS='["Kobel"]' uv run pytest          # or any entry point
KICKER_EXCLUDED_PLAYERS='["pl-k00030669", "Ramaj"]' ...     # ids and names may be mixed
```

Each entry is either a `player_id` (exact) or a case-insensitive part of a name. What the
implementation guarantees:

- **A reference matching nothing raises**, as does an ambiguous one, which names its candidates.
  A silent no-op was the failure worth engineering against: the user believes an injured player is
  excluded, the solver buys him anyway, and nothing says so.
- **Exclusions touch the pool only, never the panel** — a 2026/27 injury says nothing about the
  seasons already played, and removing him from those would bias the curve for no reason.
- **They run before `validate_pool`**, so an exclusion that leaves the pool unable to field a legal
  squad fails as a clear validation error rather than an infeasible solve.
- **Excluding a club's number one promotes his deputy to rank 1**, which is what an injury actually
  does to that club's team sheet. Verified end to end: excluding Kobel moves Ramaj from rank 2 to
  rank 1 and his projection from 26 to 161 points.

**Still open on this feature**, both small and both belonging with the CLI in §2:

- The promoted-deputy projection is an **extrapolation**. The first-choice branch is
  `174.5 + 6.8 × M`, fitted on number ones priced 1.5–4.3M; a promoted 1.0M deputy projects 161
  points from below that range. Worth flagging in the report, or clipping, rather than trusting.
- The report must **state what was excluded**, so a saved run is self-describing; and a `--exclude`
  flag that unions with the setting would save exporting an environment variable for a one-off.

---

## 1. Phase 5 — the optimizer (`optimize.py`)

The formulation is already settled in `doc/plan.md`; this is implementation. PuLP with CBC is
already a dependency. Binary `x_p` (in the 15) and `s_p` (in the scoring XI), subject to `s_p ≤ x_p`,
the squad and lineup quotas, the 30M budget and the 3-per-club cap. 1098 binaries — trivial for CBC.

Two implementation points decide whether it is correct rather than merely feasible:

- **Solve the bench jointly, not afterwards.** A 500k filler still consumes one of his club's three
  slots, so bench and XI are coupled through the club cap and cannot be chosen separately.
- **`bench_weight = 0` makes the bench cost-degenerate.** Once the optimal XI leaves any budget
  slack, *every* affordable bench is equally optimal and CBC may return an arbitrary one. Fix with a
  lexicographic two-stage solve: maximise XI points to get `P*`, then re-solve minimising total cost
  subject to `Σ ŷ_p s_p ≥ P* − tol`. That gives the cheapest squad among the point-optimal ones
  exactly, with no ε-weight to tune.

Then top-K alternatives via no-good cuts (`Σ_{p ∈ S} x_p ≤ 14` per squad already found), which is
what makes the Phase 6 robustness pass cheap.

**Tests (all specified in the plan, none written):**

- exact correctness on a hand-built ~20-player pool, brute-forced and compared against CBC;
- every returned squad satisfies all four constraint families on the real pool;
- **cheapest-bench assertion**: at `bench_weight = 0` the four bench players cost exactly 2.0M. A
  failure means either the lexicographic stage is missing or the club cap is forcing a dearer filler
  — both worth surfacing, not absorbing;
- sanity: with budget and club cap relaxed, the solver reproduces the naive top-scorer XI (54.9M).

---

## 2. Phase 6 — robustness and reporting (`report.py`, `cli.py`)

Because the choice is locked for a whole season, **a single point estimate is the wrong
deliverable** — and that matters more here than it would normally, because the outfield projection is
close to degenerate. With residual weights at ~0 the solver is nearly indifferent between ways of
spending the same money, and its answer is decided by small intercept differences. A squad presented
as "the" answer would overstate what the model actually knows.

So: Monte-Carlo sample `ŷ_p` from its predictive uncertainty, re-solve, and report each player's
**selection frequency** across draws. Players appearing in nearly every optimal squad are robust
picks; those appearing rarely are artifacts of one noisy estimate.

- `MarketCurve.residual_sd` already exists per position for exactly this.
- **`GoalkeeperModel` has no uncertainty term yet** and needs one, ideally propagating the two
  distinct sources — `P(number one)` (a Bernoulli, the dominant risk) and the points of a number one
  given that he is one. Sampling the keeper from a Normal would misrepresent a bimodal outcome.
- `report.py` emits the XI plus the four fillers with cost, projected points and selection frequency;
  `cli.py` runs load → project → optimize → report.

End-to-end check: `uv run python -m kicker_manager_analysis.cli` prints a legal 15-man squad within
30M with the XI marked.

---

## 3. Carry-over questions

- **Re-check the panel conclusions without 2024/25.** That payload is missing Bochum's and Kiel's
  players entirely (both relegated after it), so it describes a slightly stronger league. Selection
  bias, not data quality — but every persistence estimate pools over it.
- **Goalkeeper availability beyond price rank.** Rank is kicker's pricing opinion, not the coach's
  team sheet; it is a 90% proxy, not an observation. ligainsider's expected-starter signal is the
  direct measurement, and matters most for a keeper new to the league.
- **The 27% cold-start cohort.** 146 of 549 pool players have no appearance history in any of the
  three seasons. Mostly cheap (median 1.4M, only 16 above 2M), so the optimizer has little reason to
  buy them — but that should be *checked* after the optimizer exists rather than assumed.

## 4. Deferred, with a trigger

The appearance decomposition (`E[points] = E[appearances] × E[rate]`) was tested this iteration and
did not beat the plain curve; see `doc/results.md` for the numbers. Revisit when either:

- **the 2026/27 payload lands** — a third transition roughly halves the standard error on every
  persistence estimate, and is free; or
- **the optimizer exists**, at which point availability is worth retrying as a *constraint* (a floor
  on predicted appearances for anyone fielded) rather than as a multiplicative term. That uses the
  signal where it is strong — ranking who plays — without letting it multiply into the points
  estimate, which is where it failed.

Empirical-Bayes shrinkage weighted by each player's appearance count is also still untried; only the
all-or-nothing shrink was tested.
