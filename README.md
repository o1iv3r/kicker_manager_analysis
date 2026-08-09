# kicker-Managerspiel Classic — optimal squad

Works out the best 15-player Bundesliga squad you can buy for the 30M budget, given that only the
11 in the fixed 4-4-2 score points and the squad is locked for the whole season.

The budget is what makes this hard: a best XI picked purely on last season's points costs 54.9M
against the 28.0M left after the cheapest legal bench. The problem is therefore expected points
*per euro* under the squad rules, not just picking good players.

- `doc/plan.md` — the approach, phase by phase, and what is done so far
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

Save it to `data/` named `YYYY_MM_DD_spieler_daten.csv`. The date stamp matters — the loader picks
the newest file automatically, so old exports can stay in place and any run can be reproduced
against the exact pool it used. Exports are committed to the repo for that reason.

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

`residual_weight` is the one worth knowing about. The projection is
`curve + residual_weight × (last season − curve)`, so 0 trusts the kicker market values alone and 1
trusts last season's points alone. It cannot be estimated from a single export, and it changes 5 of
the top 11 players — `notebooks/projection.py` shows the sensitivity.

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
| `src/kicker_manager_analysis/projection.py` | the market curve and expected season points per player |
| `notebooks/` | marimo notebooks |
| `tests/` | pytest suite, including one test that loads the real committed export |
| `data/` | date-stamped player exports |

Optimization and reporting modules are not built yet; see `doc/plan.md` for what lands where.
