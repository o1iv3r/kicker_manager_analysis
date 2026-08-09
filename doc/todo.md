# TODO

What is left after the appearance-data iteration (see `doc/results.md`) and the optimizer that
followed it. The projection and the solver are done and validated; **there is still no report and no
CLI**, so the answer exists but nothing prints it.

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

- The promoted-deputy projection is an **extrapolation**, and Phase 5 has now seen it bite rather
  than merely predicted it. The first-choice branch is `174.5 + 6.8 × M`, fitted on number ones
  priced 1.5–4.3M; a promoted 1.0M deputy projects 161 points from below that range. Concretely:
  excluding the four cheapest rank-1 keepers promotes Markus Schubert — Paderborn's 800k number two,
  zero points last season — to rank 1, where he projects **159.4 points at a surplus of +128**,
  better than any real keeper in the pool, and the optimizer duly buys him for an XI of 1190.59
  against the true optimum of 1138.78. **A worse squad that scores higher is the failure mode to
  engineer against**, so this is no longer cosmetic: clip the first-choice branch to its fitted
  price range, or refuse to promote a deputy who has never held the shirt, rather than flagging it
  in the report. See `doc/squad.md` for the worked case.
- The report must **state what was excluded**, so a saved run is self-describing; and a `--exclude`
  flag that unions with the setting would save exporting an environment variable for a one-off.

---

## 1. Phase 5 — the optimizer — **done**

Implemented as `optimize.py`: binaries `x_p` and `s_p` with `s_p ≤ x_p`, the squad and lineup
quotas, the 30M budget and the 3-per-club cap, solved lexicographically (maximise points, then
minimise cost among the point-optimal squads) and enumerated with no-good cuts. All four tests the
plan specified are written and pass; see `doc/plan.md` for the details.

**The answer: 1138.78 projected XI points at exactly 30.00M**, bench at the 2.0M floor so the full
28.0M funds the XI, keeper Kaua Santos (2.4M, his club's number one) — the cheapest clear number
one, exactly as the goalkeeper model says to buy. Written up in `doc/squad.md`, which also records
which five of the fifteen names the model actually chooses.

Three things worth carrying forward:

- **Money must enter the programme in millions.** In euros the cost-minimising second stage never
  terminates on the real pool; in millions it takes 0.1s. It presents as a hang, not as a wrong
  answer, which is why it is recorded rather than merely fixed.
- **The degeneracy is total, not approximate.** The ten best squads tie at 1138.781568 points and
  30,000,000 euros to every digit while differing in six to nine of fifteen players. With DEF and
  FWD weights at exactly zero, two defenders at the same price are *identical* to the model. Only
  the goalkeeper, the four midfielders and spending the full 28.0M are actually decided by the
  projection.
- **The number-two goalkeeper needed no explicit constraint**, which the plan left open. The step
  model already prices him out of the XI.

**Still open**, and belonging with §2:

- The Freiburg keeper price tie (see §3) — a projection bug that Phase 5 surfaced.
- `doc/heuristic.md` is stale. It was written before the JSON panel and the goalkeeper step model,
  so its "number to beat" of 1128 was computed under a superseded projection; the same fifteen
  players score **968.2** under the current one, and its headline pick Backhaus is now ranked his
  club's number two. It has been annotated rather than rewritten, since the reasoning in it is
  still the right reasoning.

---

## 2. Phase 6 — robustness and reporting (`report.py`, `cli.py`)

Because the choice is locked for a whole season, **a single point estimate is the wrong
deliverable** — and Phase 5 turned that from a worry into a measurement. The ten best squads tie to
every digit on both points and cost while differing in most of their players, so a squad presented
as "the" answer would not merely overstate what the model knows; it would be one arbitrary draw from
a set the model cannot rank at all.

So: Monte-Carlo sample `ŷ_p` from its predictive uncertainty, re-solve, and report each player's
**selection frequency** across draws. Players appearing in nearly every optimal squad are robust
picks; those appearing rarely are artifacts of one noisy estimate.

- `MarketCurve.residual_sd` already exists per position for exactly this.
- Cost is not the obstacle: one solve on the real pool takes ~0.25s and `optimize_top_k` about
  0.3s per squad, so a few hundred draws is a minute or two.
- **`GoalkeeperModel` has no uncertainty term yet** and needs one, ideally propagating the two
  distinct sources — `P(number one)` (a Bernoulli, the dominant risk) and the points of a number one
  given that he is one. Sampling the keeper from a Normal would misrepresent a bimodal outcome.
- `report.py` emits the XI plus the four fillers with cost, projected points and selection frequency;
  `cli.py` runs load → project → optimize → report.

End-to-end check: `uv run python -m kicker_manager_analysis.cli` prints a legal 15-man squad within
30M with the XI marked.

---

## 3. Carry-over questions

- **A club that prices two keepers identically gets an arbitrary number one.** `with_keeper_rank`
  breaks ties by row order, and its docstring's claim that such a club "gets near-equal
  probabilities from the model anyway" is false — rank 1 carries `P = 0.88` and rank 2 `P = 0.08`.
  Freiburg price Atubolu and Backhaus at 3.2M each, so row order alone puts Atubolu on 182.5
  projected points and Backhaus on 41.0. It costs nothing this year — the optimum is identical to
  the digit with Atubolu excluded, because a 2.4M number one is better value than either — and no
  club-season in the panel has a tied top price, so the fit is clean. Fix by falling back to the
  previous season's appearances, or by splitting the probability between tied keepers.
- **Re-check the panel conclusions without 2024/25.** That payload is missing Bochum's and Kiel's
  players entirely (both relegated after it), so it describes a slightly stronger league. Selection
  bias, not data quality — but every persistence estimate pools over it.
- **Goalkeeper availability beyond price rank.** Rank is kicker's pricing opinion, not the coach's
  team sheet; it is a 90% proxy, not an observation. ligainsider's expected-starter signal is the
  direct measurement, and matters most for a keeper new to the league. **Phase 5 shows this is now
  the binding weakness of the whole model**, because the flat first-choice price branch makes the
  *cheapest* rank-1 keeper win, and cheap rank-1 keepers are cheap precisely because they are
  unproven. The four keepers the projection likes best have 13, 0, 0 and 2 appearances last season
  between them; the ones with 30–34 sit 5–10 surplus points behind. Since this slot carries the
  model's entire edge, using prior appearances here — as a tie-break among rank-1 keepers, not as a
  multiplicative term — is the highest-value item left on this list.
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
