# kicker-Managerspiel Classic — optimal squad

Works out the best 15-player Bundesliga squad you can buy for the 30M budget, given that only the
11 in the fixed 4-4-2 score points and the squad is locked for the whole season.

The budget is what makes this hard: a best XI picked purely on last season's points costs 54.9M
against the 28.0M left after the cheapest legal bench. The problem is therefore expected points
*per euro* under the squad rules, not just picking good players.

- `doc/plan.md` — the approach, phase by phase, and what is done so far
- `doc/heuristic.md` — a hand-built baseline squad, and the number the optimizer has to beat
- `doc/rules.md`, `doc/faq.md` — the game rules this all derives from

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.13 is installed automatically.

```bash
uv sync
```

## Data

The player pool comes from the game itself: open the Managerspiel in a browser, go to
**Transfermarkt**, and use the **Spieler-Daten-Export** link at the bottom right to download the
CSV.

Save it to `data/` named `YYYY_MM_DD_spieler_daten.csv` (the historical files carry only a year).
The stamp matters — the loader picks the newest file automatically, so old exports can stay in
place and any run can be reproduced against the exact pool it used. Exports are committed to the
repo for that reason.

Old exports are not just archive. Each file's club list gives the squads for the season ahead
while its points give the season just finished, so the files together form a panel of
`(price for a season, points scored in that season)` — which is what the model is fitted on. An
export taken *before* a season starts repeats the previous season's points against new prices;
`load_panel` detects those and leaves them out of the fit, keeping the pairing honest.

### Appearances

`data/json/{2023,2024,2025}.json` are the game's own API payloads for the same three seasons. They
reconcile against the CSVs exactly — same players, same prices, same points — but each player also
carries a `ratingBreakDown` that splits his season total into counts: `starter` and `joker`
(**appearances = their sum**), goals, assists, clean sheets, player-of-the-match awards and cards,
each alongside the points it contributed.

This is the denominator the CSV lacks, and it is what makes `points ÷ appearances` computable. It
matters because the two factors behave differently: after price, how *well* a player performs per
appearance is unpredictable year over year at every position, while how *often* he plays persists.
See `doc/plan.md` for the measured numbers and for the out-of-sample test that this decomposition
still has to win.

The JSON also carries fixtures (`matches`, `rounds`), but without lineups or results — per-matchday
data exists only in `data/additional_match_data/`, and only for 2024/25.

## Notebooks

Exploratory work uses [marimo](https://marimo.io), not Jupyter.

- `notebooks/eda.py` — what the export's columns actually mean
- `notebooks/projection.py` — how the market curve is fitted, and how much the answer moves

```bash
uv run marimo edit notebooks/eda.py   # interactive editor (starts a local server, opens a browser)
uv run marimo run notebooks/eda.py    # read-only app view
uv run python notebooks/eda.py        # execute headlessly; non-zero exit if any cell raises
uv run marimo check notebooks/eda.py  # lint the notebook (--fix to apply)
```

marimo notebooks are plain Python modules, so they diff and merge like normal code. Editing one in
the browser rewrites the file on disk.

## Quality checks

Run all four before calling a task done:

```bash
uv run ruff check .
uv run ruff format .
uv run mypy
uv run pytest
```

A single test: `uv run pytest tests/test_data.py::test_real_export_loads_and_validates`

## Configuration

Rules and paths live in `Settings` (`src/kicker_manager_analysis/config.py`) and default to the
Bundesliga variant. Any field can be overridden with a `KICKER_`-prefixed environment variable:

```bash
KICKER_BUDGET=7500000 uv run pytest          # 2. Bundesliga budget
KICKER_BENCH_WEIGHT=0.25 uv run pytest       # stop treating the bench as worthless
KICKER_RESIDUAL_WEIGHT=1.0 uv run pytest     # trust last season fully over the market curve
```

`residual_weight` is normally left unset. The projection is
`baseline + residual_weight × (last season's residual)`, and the weight is **measured** from the
multi-season panel rather than chosen — it comes out at ~0 for outfield players and ~0.24 for
goalkeepers. Set it only to explore how the answer moves.

The baseline is the market curve for outfield players, but **goalkeepers get their own model**.
Keeping is a step function on who plays rather than a line on price: a club's most expensive keeper
plays 27.9 matches a season and his deputy 4.0, so the projection is
`P(number one | within-club price rank) × points of a number one`. Among the 49 number ones in the
panel, points correlate with price at −0.03 — so the rule is to buy the *cheapest* clear number one,
not the best keeper.

Quotas take JSON, which is how to try a formation other than 4-4-2:

```bash
KICKER_LINEUP_QUOTA='{"GOALKEEPER":1,"DEFENDER":3,"MIDFIELDER":5,"FORWARD":2}' uv run pytest
```

Settings are validated and frozen: a lineup that asks for more players in a position than the squad
holds, or a quota missing a position, is rejected at construction rather than producing an illegal
squad later. (Note that Classic only offers 4-4-2 — the flexibility is for exploring what the
constraint costs, and for testing the optimizer against small pools.)

## Layout

| Path | Contents |
|---|---|
| `src/kicker_manager_analysis/scoring.py` | the kicker scoring rules and the `Position` enum |
| `src/kicker_manager_analysis/config.py` | `Settings`: budget, quotas, club cap, bench weight |
| `src/kicker_manager_analysis/data.py` | export discovery, loading, schema and pool validation |
| `src/kicker_manager_analysis/projection.py` | the market curve, the goalkeeper model and expected season points per player |
| `src/kicker_manager_analysis/backtest.py` | held-out-season scoring against the baselines the model must beat |
| `notebooks/` | marimo notebooks |
| `tests/` | pytest suite, including one test that loads the real committed export |
| `data/` | date-stamped player exports (CSV) |
| `data/json/` | per-season API payloads: appearances and the full points decomposition |
| `data/additional_match_data/` | per-match player rows, 2024/25 only |

Optimization and reporting modules are not built yet; see `doc/plan.md` for what lands where.
