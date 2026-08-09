# Baseline heuristic squad

A hand-built squad to measure the optimizer against, derived from section 4 of
`notebooks/projection.py`. Any solver output that cannot beat this is not earning its keep.

## The rule

- **Goalkeeper: Mio Backhaus.**
- **Defenders at around 2.0M.**
- **The two most expensive strikers affordable.**
- **Four midfielders at roughly the same average price.**
- **Four replacements at 500k each.**

## One instance of it

Exactly 30.00M, 28.00M of it in the scoring XI. Legal on all four constraint families:
2/5/5/3 squad, 1/4/4/2 lineup, budget, and three-per-club (Augsburg sits on the cap).

| | player | club | price | projected |
|---|---|---|---|---|
| GK | **Mio Backhaus** | SC Freiburg | 3.2M | 201.0 |
| DEF | Jeffrey Gouweleeuw | FC Augsburg | 2.0M | 76.3 |
| DEF | Danny da Costa | 1. FSV Mainz 05 | 2.0M | 76.3 |
| DEF | Emre Can | Borussia Dortmund | 2.0M | 76.3 |
| DEF | Leopold Querfeld | 1. FC Union Berlin | 2.0M | 76.3 |
| MID | Han-Noah Massengo | FC Augsburg | 2.0M | 76.1 |
| MID | Nicolai Remberg | Hamburger SV | 2.0M | 75.7 |
| MID | Yannik Engelhardt | SC Freiburg | 2.0M | 75.2 |
| MID | Bambasé Conté | TSG Hoffenheim | 2.0M | 74.5 |
| FWD | Luis Diaz | Bayern München | 7.0M | 261.4 |
| FWD | Edin Dzeko | FC Schalke 04 | 1.8M | 58.9 |
| bench | Tobias Sippel | Bor. Mönchengladbach | 0.5M | — |
| bench | Timothy Chandler | Eintracht Frankfurt | 0.5M | — |
| bench | Max Grüger | FC Schalke 04 | 0.5M | — |
| bench | Thomas Kastanaras | FC Augsburg | 0.5M | — |

**XI projected points: 1128.**

## The one adjustment the budget forced

"The two most expensive strikers" cannot be taken literally. Kane (10.0M) and Olise (8.5M)
together are 18.5M; with Backhaus at 3.2M, four defenders at 2.0M and a 2.0M bench that is
**31.7M before a single midfielder**, against a 30M budget. The rule is applied here as *the most
expensive pair affordable once the other slots are funded*, which leaves 8.8M and yields Diaz
plus a cheap partner.

## Why this scores as well as it does

Under the current projection the outfield blend weight is zero, so an outfield player's expected
points are fixed by his price and position alone. The XI's positional mix is fixed by the 4-4-2.
That makes the whole XI total collapse to:

```
XI points  =  -32.4  +  38.94 x (XI spend in millions)  +  0.418 x (goalkeeper residual)
                                                        +  0.024 x (midfield residuals)
```

Checks out on the squad above: `-32.4 + 38.94 x 28.0 = 1057.9`, plus 66.7 from Backhaus and 3.5
from the midfielders, giving 1128.

Two consequences worth being blunt about.

**Only two decisions in this squad actually matter.** Spend the maximum on the XI — that means the
bench at the 500k floor, so 28.0M reaches the eleven — and pick the right goalkeeper. Everything
else is model-indifferent.

**The goalkeeper choice is the good part, and it is not arbitrary.** Ranking every player by
surplus over the market rate (`projected points - 38.94 x price`), Backhaus tops the *entire
pool*, and the top eight are all keepers:

| player | club | price | surplus |
|---|---|---|---|
| **Mio Backhaus** | SC Freiburg | 3.2M | **+76.4** |
| Daniel Heuer Fernandes | Hamburger SV | 3.2M | +71.4 |
| Moritz Nicolas | Bor. Mönchengladbach | 3.0M | +65.2 |
| Daniel Batz | Bor. Mönchengladbach | 1.0M | +59.2 |
| Marvin Schwäbe | 1. FC Köln | 3.0M | +52.7 |

The best midfielder in the pool scores **+0.6** on the same measure. That is the whole story: the
goalkeeper slot is the only one where the model knows something the price does not.

## What is arbitrary here, and what is not

The named defenders and midfielders are **placeholders**. Every defender priced at 2.0M carries an
identical projection, so the model cannot choose between them and swapping any of them for another
at the same price changes the total by nothing. The same applies to the forward split: two 4.4M
strikers score exactly what Diaz plus Dzeko scores, because only the sum matters. Pick among
equals on football knowledge — availability, injury risk, penalty duties — none of which is in
this data.

What is *not* arbitrary: the goalkeeper, and spending the full 28.0M on the XI.

## Known weaknesses

- **The 2.0M defender level is not justified by the model.** With a near-flat curve there is no
  optimal price point; 2.0M is a reasonable spread, not a derived answer. Its real merit is
  diversification, which the projection does not score.
- **Concentration risk is unpriced.** Diaz at 7.0M is a quarter of the XI budget on one player.
  The projection is a point estimate with no variance term, so it cannot see that. Phase 6's
  Monte-Carlo pass is what should settle whether the concentration is worth it.
- **The goalkeeper edge rests on 70 transitions.** The +0.45 residual persistence is the strongest
  signal in the model but comes from a modest sample, and it assumes Backhaus stays Freiburg's
  first choice — which the export cannot confirm and ligainsider could.
- **Bench players are assumed worthless.** True by design at `bench_weight = 0`, and for the
  reserve keeper it is close to literally true, since keepers are rarely substituted.

## How to use it

When `optimize.py` lands, this is the number to beat: **1128 XI points at 30.00M**. Given the
decomposition above, a correct solver should land very close to it and differ mainly by picking
the same goalkeeper and spending the same 28.0M — if it comes back far higher, suspect a
constraint is not binding.
