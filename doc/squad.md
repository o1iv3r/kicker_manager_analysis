# The recommended squad, 2026/27

**1138.78 projected XI points for exactly 30.00M**, produced by `optimize.py` against the
`2026_08_09` export. This supersedes `doc/heuristic.md`, whose numbers predate the current
projection.

Read the second half before buying anything. Most of this squad is not a recommendation — the model
is exactly indifferent between the six outfield names below and thousands of alternatives, and
saying so is more useful than the table.

## One instance of the optimum

| | player | club | price | projected |
|---|---|---|---|---|
| GK | **Kaua Santos** | Eintracht Frankfurt | 2.4M | 169.9 |
| DEF | Nico Elvedi | Bor. Mönchengladbach | 2.4M | 91.9 |
| DEF | Gideon Mensah | 1. FC Köln | 2.0M | 76.3 |
| DEF | Timo Becker | FC Schalke 04 | 1.7M | 64.7 |
| DEF | Anthony Jung | SC Freiburg | 1.2M | 45.2 |
| MID | **Christoph Baumgartner** | RB Leipzig | 4.2M | 164.2 |
| MID | **Said El Mala** | 1. FC Köln | 4.2M | 163.9 |
| MID | **Andrej Kramaric** | TSG Hoffenheim | 4.2M | 163.2 |
| MID | **Aleix Garcia** | Bayer 04 Leverkusen | 4.0M | 155.6 |
| FWD | Kennedy Okpala | SC Paderborn 07 | 1.2M | 35.6 |
| FWD | Robert Ramsak | RB Leipzig | 0.5M | 8.3 |
| bench | Patrick Drewes | Borussia Dortmund | 0.5M | — |
| bench | Luis Seifert | SV Elversberg | 0.5M | — |
| bench | Viggo Gebel | RB Leipzig | 0.5M | — |
| bench | Imad Rondic | 1. FC Köln | 0.5M | — |

XI 28.00M, bench 2.00M, 30.00M total. Legal on all four constraint families; Köln and Leipzig sit
on the three-per-club cap. **Bold** marks the five players the model actually chooses — see below.

## What is decided, and what is free

Enumerating the ten best squads settles this rather than leaving it to intuition. All ten score
**1138.781568 points at 30,000,000 euros — identical to every digit** — while differing in six to
nine of their fifteen players. Every one of them contains the same goalkeeper and the same four
midfielders, and in every one the four defenders and two forwards together cost **exactly 9.0M**.

That is not a solver quirk. With the measured defender and forward blend weights at exactly zero,
an outfield player's projection is `intercept(position) + 38.94 × price`, and the 4-4-2 fixes how
many of each position are in the XI. So the XI total collapses to

```
XI points  =  (goalkeeper)  +  (four midfielders)  +  const  +  38.94 x (defender + forward spend)
```

and the six defenders and forwards enter only through their **sum**. Two defenders at the same price
are not similar to the model, they are *identical*.

**So the actual instruction is:**

1. **Kaua Santos in goal, 2.4M** — or another keeper, which is the one decision worth arguing about.
2. **Those four midfielders, 16.6M.** Worth +13.6 points against four average midfielders costing
   the same, which is the model's entire outfield edge.
3. **Any four defenders and two forwards totalling 9.0M.** Pick them on football knowledge —
   availability, injury history, penalty and set-piece duty, whether a 500k forward will see the
   pitch. None of that is in this data, and the model gives up nothing by letting you decide.
4. **Any four players at 500k, one per position, on the bench.** The pool holds 25/16/12/13 of them,
   so this is unconstrained beyond the club cap.

Verified rather than argued: swapping the four defenders and two forwards above for four 500k
defenders plus Serge Gnabry (4.2M) and Romulo (2.8M) — a completely different shape, same 9.0M —
scores **1138.7816**, the same number.

The ceiling on any single defender or forward is **6.5M**, since the other five must cost at least
500k each. That makes Deniz Undav (6.0M) or Jonathan Tah (4.3M) free to field. Harry Kane (10.0M)
and Luis Diaz (7.0M) are not reachable in a point-optimal squad.

The midfield is pinned far more weakly than the goalkeeper: the fourth choice (Kramaric) beats the
fifth (Leon Avdullahu, 3.2M) by **0.05 points**. Treat the midfield four as "any four from the top
six or so", not as a verdict.

## The goalkeeper, which is the only real decision — and its weak spot

Everything the model knows beyond the price is in this slot. A rank-1 keeper is worth roughly 125
points more than the same money spent anywhere else, because his club's number one plays ~28 matches
and his deputy plays ~4.

But the ranking *within* rank-1 keepers is driven almost entirely by cheapness, because the
first-choice branch is nearly flat in price (`174.5 + 6.8 × M`). The result is uncomfortable:

| keeper | club | price | projected | surplus | 25/26 rank | 25/26 apps | 25/26 pts |
|---|---|---|---|---|---|---|---|
| **Kaua Santos** | Eintracht Frankfurt | 2.4M | 169.9 | **+76.4** | 2 | 13 | 30 |
| Nicolas Kristof | SV Elversberg | 2.4M | 169.0 | +75.5 | — | — | — |
| Nahuel Noll | SC Paderborn 07 | 2.4M | 169.0 | +75.5 | — | — | — |
| Karl Hein | Werder Bremen | 2.4M | 167.3 | +73.9 | 2 | 2 | 18 |
| Moritz Nicolas | Bor. Mönchengladbach | 3.0M | 188.6 | +71.8 | **1** | **34** | 246 |
| Finn Dahmen | FC Augsburg | 2.6M | 172.5 | +71.3 | **1** | **34** | 185 |
| Frederik Rönnow | 1. FC Union Berlin | 2.6M | 170.3 | +69.0 | **1** | **30** | 173 |
| Daniel Heuer Fernandes | Hamburger SV | 3.2M | 191.6 | +66.9 | **1** | **33** | 253 |

The model's top four have never held the shirt: Santos has been Frankfurt's number two or three in
all three panel seasons and played 13 matches last year, while the man he is now priced above —
Michael Zetterer, 1.5M — was Frankfurt's rank-1 keeper with 22 appearances and 102 points. Kristof
and Noll were not in the league at all. Meanwhile the keepers with 30-34 appearances of evidence sit
5 to 10 surplus points behind.

The model cannot see this, by construction. `keeper_rank` is kicker's *pricing opinion* about who
starts, not an observation of who started, and the projection deliberately does not use prior
appearances (Phase 4's decomposition was tested and did not ship). Yet appearances are the one
channel the panel shows to persist beyond price, at **+0.573 for keepers**.

**The trade is cheap.** Buying Moritz Nicolas instead of Santos costs 0.6M, which is 23.4 points of
outfield spend against 18.7 more from the keeper — a net **−4.6 points, 0.4% of the total** — and
buys a keeper with 34 appearances and 246 points last season instead of 13 and 30. Given that this
slot carries the model's whole edge, that looks like a good price for the evidence. It is a judgement
call the data cannot make, so it is left here rather than hard-coded.

Cross-checking the intended starter against ligainsider before committing is the cheapest risk
reduction available on this page.

### Do not use `KICKER_EXCLUDED_PLAYERS` to shop for a keeper

Exclusions apply before the rank is computed, so removing a club's number one **promotes his deputy
to rank 1** — correct for the injury case it was built for, wrong as a way to skip a keeper you
merely dislike. Excluding the four unproven keepers above returns a squad scoring **1190.59**, which
looks like an improvement and is an artefact: it promotes Markus Schubert (Paderborn's 800k number
two, zero points last season) to rank 1, where the flat first-choice branch extrapolates him to
159.4 projected points and a surplus of +128 — better than any real keeper in the pool. This is the
extrapolation hazard `doc/todo.md` §0 anticipated, now observed. Reserve exclusions for players who
genuinely cannot be bought.

## Reproducing

```bash
uv run python -c "
from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.data import load_latest_players, load_panel
from kicker_manager_analysis.projection import fit_and_project
from kicker_manager_analysis.optimize import optimize_top_k, squad_frame
s = Settings()
projected, _ = fit_and_project(load_panel(s.data_dir), load_latest_players(s), s)
for squad in optimize_top_k(projected, s, 5):
    print(squad.lineup_points, squad.cost)
    print(squad_frame(projected, squad).select('name', 'club', 'position',
                                               'market_value', 'projected_points', 'in_lineup'))"
```

## Caveats a reviewer should hold onto

- **The margin over doing something sensible by hand is about 1%.** Against `doc/heuristic.md`'s own
  scoring the optimizer gains 10.8 points, all of it midfield residuals. The solver's value is in
  proving the budget is spent optimally, not in finding players a careful human would miss.
- **The outfield answer is not a recommendation at all**, per the section above. Anyone reading the
  table as fifteen picks has read it wrong.
- **The projection is a point estimate with no variance term.** Phase 6's Monte-Carlo pass is what
  should turn the tie above into selection frequencies, and it is the honest deliverable for a
  decision locked for a whole season.
- **One club prices two keepers identically.** Freiburg's Atubolu and Backhaus are both 3.2M, and
  row order alone decides which is projected at 182.5 and which at 41.0. It does not affect this
  squad — neither is bought — but see `doc/todo.md` §3.
- **Every number here is model output, not a forecast of the season.** The backtest puts the
  projection's RMSE at ~52 points per player against baselines at 53-65, so a 1138-point XI carries
  error bars far wider than the 4.6 points separating the keeper options.
