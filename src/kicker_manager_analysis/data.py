"""Loading and validation of the kicker player-data export.

The export is downloaded manually from the Transfermarkt page of the kicker-Managerspiel and
saved to ``data/`` under a ``YYYY_MM_DD_spieler_daten.csv`` name. Keeping the date stamp lets a
run be reproduced against the exact pool it used, so the loader resolves the newest file rather
than a fixed path.
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
EXPORT_FILENAME_PATTERN: Final = re.compile(r"^(\d{4})_(\d{2})_(\d{2})_spieler_daten\.csv$")

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
        path: Path to a ``YYYY_MM_DD_spieler_daten.csv`` file.

    Returns:
        The date the export was downloaded.

    Raises:
        ValueError: If the filename does not carry a parseable date stamp.
    """
    match = EXPORT_FILENAME_PATTERN.match(path.name)
    if match is None:
        raise ValueError(f"{path.name!r} is not a YYYY_MM_DD_spieler_daten.csv export")
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)


def latest_export(data_dir: Path) -> Path:
    """Return the most recently dated player export in ``data_dir``.

    Args:
        data_dir: Directory holding the date-stamped exports.

    Returns:
        Path to the newest export.

    Raises:
        FileNotFoundError: If the directory holds no parseable export.
    """
    exports = [path for path in sorted(data_dir.glob(EXPORT_GLOB)) if _is_export(path)]
    if not exports:
        raise FileNotFoundError(f"no {EXPORT_GLOB} export found in {data_dir}")
    return max(exports, key=export_date)


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

    Renames the German source columns, drops the ones the model does not use, and casts the
    position to an enum so that an unknown value cannot reach the optimizer.

    Args:
        path: Path to a kicker player-data export.

    Returns:
        A frame with columns ``player_id``, ``name``, ``club``, ``position``, ``market_value``,
        ``points`` and ``grade_average``.

    Raises:
        ValueError: If columns are missing, ids are duplicated, values are null, market values
            are not positive, or a position is not one recognised by the game.
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
    )

    _reject_invalid_rows(players, path.name)
    return players


def _reject_invalid_rows(players: pl.DataFrame, source: str) -> None:
    """Raise if the frame carries nulls, duplicate ids, or non-positive market values.

    Args:
        players: Canonical player frame.
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
