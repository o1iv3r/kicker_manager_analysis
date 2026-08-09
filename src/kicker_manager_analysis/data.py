"""Loading and validation of the kicker player-data export.

The export is downloaded manually from the Transfermarkt page of the kicker-Managerspiel and
saved to ``data/`` under a ``YYYY_MM_DD_spieler_daten.csv`` name; the historical files carry only
a year. Keeping the stamp lets a run be reproduced against the exact pool it used, so the loader
resolves the newest file rather than a fixed path.

The exports serve two distinct purposes and must not be confused.
:func:`load_latest_players` gives the **pool to pick from** — the squads and prices of the season
being played. :func:`load_panel` gives the **fitting sample**, every export whose points describe
its own season, which is not all of them: an export taken before kickoff reports the season just
finished against the prices of the season to come. See :func:`is_repriced_repeat`.
"""

import re
from datetime import date
from math import ceil
from pathlib import Path
from typing import Final

import polars as pl

from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.scoring import Position

EXPORT_GLOB: Final = "*_spieler_daten.csv"
EXPORT_FILENAME_PATTERN: Final = re.compile(r"^(\d{4})(?:_(\d{2})_(\d{2}))?_spieler_daten\.csv$")
"""``YYYY_spieler_daten.csv`` or ``YYYY_MM_DD_spieler_daten.csv``.

The historical exports carry only the year. The leading year is the season key either way; the
month and day merely order several exports taken within one season.
"""

UNPURCHASABLE_MARKET_VALUE: Final = 999_000_000
"""Sentinel kicker uses for a player who cannot be bought.

Not a price: 60 rows in the 2024 export and 12 in the 2025 one carry it, essentially all with zero
points, marking players unregistered, long-term injured, or arrived after pricing. Left in place it
is a colossal leverage point on any regression through market value, and it silently corrupts
within-club price rank — the statistic the goalkeeper model rests on.
"""

REPEAT_SHARE_THRESHOLD: Final = 0.9
"""Share of shared point totals above which an export is a repricing, not a new season.

The gap is wide enough that the exact cut does not matter: the 2026 export repeats 356 of 356
overlapping players, while real transitions repeat 10 of 290 and 25 of 286.
"""

MAX_PLAUSIBLE_MARKET_VALUE: Final = 20_000_000
"""Ceiling a real price cannot reach, given a 30M budget for fifteen players.

Guards against kicker introducing a *different* sentinel: anything above this is a coding
convention rather than a valuation, and should fail loudly instead of skewing a fit.
"""

COLUMN_MAPPING: Final[dict[str, str]] = {
    "ID": "player_id",
    "Angezeigter Name": "name",
    "Verein": "club",
    "Position": "position",
    "Marktwert": "market_value",
    "Punkte": "points",
    "Notendurchschnitt": "grade_average",
}
"""Source column -> canonical column. Columns outside this mapping are dropped."""

POSITION_DTYPE: Final = pl.Enum([position.value for position in Position])


def export_date(path: Path) -> date:
    """Parse the date stamp out of an export filename.

    Args:
        path: Path to a ``YYYY_spieler_daten.csv`` or ``YYYY_MM_DD_spieler_daten.csv`` file.

    Returns:
        The date the export was downloaded. A year-only filename yields the first of January,
        which orders it ahead of any dated export from the same year but is not a real download
        date — use :func:`export_season` when the season is what matters.

    Raises:
        ValueError: If the filename does not carry a parseable date stamp.
    """
    match = EXPORT_FILENAME_PATTERN.match(path.name)
    if match is None:
        raise ValueError(f"{path.name!r} is not a YYYY[_MM_DD]_spieler_daten.csv export")
    year, month, day = match.groups()
    return date(int(year), int(month or 1), int(day or 1))


def export_season(path: Path) -> int:
    """Return the season an export belongs to, as its leading year.

    Args:
        path: Path to an export.

    Returns:
        The year in the filename, which keys the season the squads were assembled for.

    Raises:
        ValueError: If the filename does not carry a parseable date stamp.
    """
    return export_date(path).year


def latest_export(data_dir: Path) -> Path:
    """Return the most recently dated player export in ``data_dir``.

    Args:
        data_dir: Directory holding the date-stamped exports.

    Returns:
        Path to the newest export.

    Raises:
        FileNotFoundError: If the directory holds no parseable export.
    """
    return all_exports(data_dir)[-1]


def _is_export(path: Path) -> bool:
    """Report whether a path carries a parseable export filename.

    Args:
        path: Candidate file.

    Returns:
        True if the filename matches the export naming convention.
    """
    return EXPORT_FILENAME_PATTERN.match(path.name) is not None


def load_players(path: Path) -> pl.DataFrame:
    """Read one export into the canonical player frame.

    Renames the German source columns, drops the ones the model does not use, casts the position
    to an enum so that an unknown value cannot reach the optimizer, and drops players carrying
    the :data:`UNPURCHASABLE_MARKET_VALUE` sentinel — they could not be bought that season, so
    they belong in neither the pool nor a fit.

    Args:
        path: Path to a kicker player-data export.

    Returns:
        A frame with columns ``player_id``, ``name``, ``club``, ``position``, ``market_value``,
        ``points`` and ``grade_average``.

    Raises:
        ValueError: If columns are missing, ids are duplicated, values are null, market values
            are not positive or implausibly large, or a position is not one recognised by the
            game.
    """
    frame = pl.read_csv(path, separator=";", encoding="utf8")

    missing = [column for column in COLUMN_MAPPING if column not in frame.columns]
    if missing:
        raise ValueError(f"{path.name} is missing expected columns: {missing}")

    players = frame.select(
        pl.col(source).alias(target) for source, target in COLUMN_MAPPING.items()
    )

    unknown = set(players.get_column("position").unique()) - {
        position.value for position in Position
    }
    if unknown:
        raise ValueError(f"{path.name} contains unknown positions: {sorted(unknown)}")

    players = players.with_columns(
        pl.col("position").cast(POSITION_DTYPE),
        pl.col("market_value").cast(pl.Int64),
        pl.col("points").cast(pl.Int64),
        pl.col("grade_average").cast(pl.Float64),
    ).filter(pl.col("market_value") != UNPURCHASABLE_MARKET_VALUE)

    _reject_invalid_rows(players, path.name)
    return players


def _reject_invalid_rows(players: pl.DataFrame, source: str) -> None:
    """Raise if the frame carries nulls, duplicate ids, or unusable market values.

    Args:
        players: Canonical player frame, already stripped of the known sentinel.
        source: Filename used in error messages.

    Raises:
        ValueError: If any of those conditions holds.
    """
    null_columns = [
        column for column, count in players.null_count().row(0, named=True).items() if count
    ]
    if null_columns:
        raise ValueError(f"{source} has null values in: {null_columns}")

    duplicates = players.get_column("player_id").is_duplicated().sum()
    if duplicates:
        raise ValueError(f"{source} has {duplicates} duplicated player ids")

    non_positive = players.filter(pl.col("market_value") <= 0).height
    if non_positive:
        raise ValueError(f"{source} has {non_positive} players with a non-positive market value")

    implausible = players.filter(pl.col("market_value") > MAX_PLAUSIBLE_MARKET_VALUE)
    if implausible.height:
        largest = int(implausible.get_column("market_value").to_numpy().max())
        raise ValueError(
            f"{source} has {implausible.height} players priced above "
            f"{MAX_PLAUSIBLE_MARKET_VALUE} (largest {largest}); this looks like an unhandled "
            f"sentinel rather than a valuation"
        )


def validate_pool(players: pl.DataFrame, settings: Settings) -> None:
    """Check that the pool could produce a legal squad at all.

    These are necessary conditions, deliberately cheap: the per-position minimum cost ignores
    the club cap, so passing here does not guarantee the optimizer will find a feasible squad.
    Failing here means it certainly will not, which is worth catching before a solve.

    Args:
        players: Canonical player frame.
        settings: Rules the pool has to satisfy.

    Raises:
        ValueError: If a position is under-supplied, too few clubs are represented to respect
            the club cap, or the cheapest legal squad already exceeds the budget.
    """
    available = dict(players.group_by("position").len().iter_rows())
    short = {
        position: (available.get(position.value, 0), required)
        for position, required in settings.squad_quota.items()
        if available.get(position.value, 0) < required
    }
    if short:
        detail = ", ".join(
            f"{pos}: have {have}, need {need}" for pos, (have, need) in short.items()
        )
        raise ValueError(f"pool cannot fill the squad quota ({detail})")

    clubs = players.get_column("club").n_unique()
    clubs_needed = ceil(settings.squad_size / settings.club_cap)
    if clubs < clubs_needed:
        raise ValueError(
            f"pool has {clubs} clubs but a squad of {settings.squad_size} with a cap of "
            f"{settings.club_cap} per club needs at least {clubs_needed}"
        )

    if (cheapest := minimum_squad_cost(players, settings)) > settings.budget:
        raise ValueError(
            f"cheapest possible squad costs {cheapest} but the budget is {settings.budget}"
        )


def minimum_squad_cost(players: pl.DataFrame, settings: Settings) -> int:
    """Return the cost of the cheapest quota-satisfying squad, ignoring the club cap.

    A lower bound on what any legal squad can cost, used both for feasibility checks and as the
    reference the cheapest-bench assertion is measured against.

    Args:
        players: Canonical player frame.
        settings: Rules supplying the squad quota.

    Returns:
        Total market value in euros.
    """
    return sum(
        int(
            players.filter(pl.col("position") == position.value)
            .get_column("market_value")
            .sort()
            .head(required)
            .sum()
        )
        for position, required in settings.squad_quota.items()
    )


def all_exports(data_dir: Path) -> list[Path]:
    """Return every parseable export in ``data_dir``, oldest first.

    Args:
        data_dir: Directory holding the exports.

    Returns:
        Export paths in chronological order.

    Raises:
        FileNotFoundError: If the directory holds no parseable export.
    """
    exports = [path for path in sorted(data_dir.glob(EXPORT_GLOB)) if _is_export(path)]
    if not exports:
        raise FileNotFoundError(f"no {EXPORT_GLOB} export found in {data_dir}")
    return sorted(exports, key=export_date)


def is_repriced_repeat(newer: pl.DataFrame, older: pl.DataFrame) -> bool:
    """Report whether an export repeats the previous season's results against new prices.

    An export downloaded before a season starts carries the squads and prices of the season to
    come but the *points of the season just finished* — the same points the previous export
    already reported. Training on such a file pairs one season's prices with another season's
    results, which silently estimates the wrong quantity, so it has to be detected rather than
    assumed away. Genuine season-to-season transitions share 3-9% of point totals; a repeat
    shares essentially all of them.

    Args:
        newer: Canonical frame from the later export.
        older: Canonical frame from the export immediately before it.

    Returns:
        True if the overlapping players carry the same points and grades in both.
    """
    overlap = newer.join(older, on="player_id", suffix="_previous")
    if overlap.is_empty():
        return False
    identical = overlap.filter(
        (pl.col("points") == pl.col("points_previous"))
        & (pl.col("grade_average") == pl.col("grade_average_previous"))
    ).height
    return identical / overlap.height >= REPEAT_SHARE_THRESHOLD


def load_panel(data_dir: Path) -> pl.DataFrame:
    """Load every export whose points describe its own season, stacked into one frame.

    This is the fitting sample: each row pairs the price a player carried for a season with the
    points he went on to score *in that same season*. Exports that merely reprice the previous
    season's results are dropped, as are the market values that never applied.

    Args:
        data_dir: Directory holding the exports.

    Returns:
        The canonical player columns plus ``season``, the year the squads were assembled for.

    Raises:
        FileNotFoundError: If the directory holds no parseable export.
        ValueError: If no export survives as a usable season.
    """
    frames = [(path, load_players(path)) for path in all_exports(data_dir)]
    seasons = [
        (path, frame)
        for index, (path, frame) in enumerate(frames)
        if index == 0 or not is_repriced_repeat(frame, frames[index - 1][1])
    ]
    if not seasons:
        raise ValueError(f"no export in {data_dir} carries results for its own season")
    return pl.concat(
        frame.with_columns(pl.lit(export_season(path), dtype=pl.Int32).alias("season"))
        for path, frame in seasons
    )


def load_latest_players(settings: Settings) -> pl.DataFrame:
    """Load and validate the newest export in the configured data directory.

    Args:
        settings: Supplies the data directory and the rules to validate against.

    Returns:
        The validated canonical player frame.
    """
    players = load_players(latest_export(settings.data_dir))
    validate_pool(players, settings)
    return players
