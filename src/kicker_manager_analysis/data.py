"""Loading and validation of the kicker player data.

Two sources, serving two purposes that must not be confused.

:func:`load_latest_players` gives the **pool to pick from**, and comes from the CSV downloaded
manually from the Transfermarkt page of the kicker-Managerspiel, saved to ``data/`` under a
``YYYY_MM_DD_spieler_daten.csv`` name. Keeping the stamp lets a run be reproduced against the exact
pool it used, so the loader resolves the newest file rather than a fixed path.

:func:`load_panel` gives the **fitting sample**, and comes from the game's own API payloads under
``data/json/``, one file per completed season. Those carry the same players, prices and points as
the CSVs — the reconciliation is exact, and asserted in the tests — but they additionally break each
season total down into its counts, of which **appearances** is the one the model could not otherwise
obtain. A season's payload pairs the price a player carried with the points he went on to score in
that same season, so unlike the CSVs there is no repricing hazard to detect.
"""

import json
import re
from datetime import date
from math import ceil
from pathlib import Path
from typing import Any, Final

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

SEASON_DIR_NAME: Final = "json"
"""Subdirectory of the data directory holding the per-season API payloads."""

SEASON_FILENAME_PATTERN: Final = re.compile(r"^(\d{4})\.json$")
"""``YYYY.json``, the year being the season the payload describes."""

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

BREAKDOWN_COMPONENTS: Final = (
    "ratingGrade",
    "ratingGoals",
    "ratingCards",
    "ratingAssists",
    "ratingStarter",
    "ratingMvp",
    "ratingCleanSheet",
    "ratingJoker",
)
"""The eight scoring channels a season total decomposes into.

They must sum to ``ratingSum``, which must in turn equal the player's ``rating``. This holds for
every row of every payload today; a violation would mean the breakdown and the total describe
different things, and the appearance counts taken from that breakdown could not be trusted.
"""


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


def resolve_player(players: pl.DataFrame, reference: str) -> str:
    """Resolve one id-or-name reference to exactly one player id.

    Args:
        players: Canonical player frame to search.
        reference: A ``player_id``, matched exactly, or part of a name, matched case-insensitively.

    Returns:
        The matching ``player_id``.

    Raises:
        ValueError: If the reference matches no player, or more than one. Both are mistakes worth
            stopping for — a reference that quietly matches nothing would leave a player in the
            pool that the caller believes they removed.
    """
    if players.filter(pl.col("player_id") == reference).height == 1:
        return reference

    matches = players.filter(
        pl.col("name").str.to_lowercase().str.contains(reference.lower(), literal=True)
    )
    if matches.height == 1:
        return str(matches.get_column("player_id").item())
    if matches.is_empty():
        raise ValueError(f"{reference!r} matches no player in the pool")

    candidates = ", ".join(
        f"{name} ({club}, {player_id})"
        for player_id, name, club in matches.select("player_id", "name", "club").head(6).iter_rows()
    )
    raise ValueError(f"{reference!r} matches {matches.height} players: {candidates}")


def apply_exclusions(players: pl.DataFrame, settings: Settings) -> pl.DataFrame:
    """Drop the players named in ``settings.excluded_players`` from the pool.

    This applies to the **pool only**, never to the fitting panel: a player being injured for the
    season to come says nothing about the seasons already played, and removing him from those
    would bias the curve and the goalkeeper probabilities for no reason.

    It also runs *before* :func:`validate_pool` and before any ranking, so that exclusions which
    make the pool unable to field a legal squad fail as a validation error, and so that excluding
    a club's number one promotes his deputy to price rank 1 — which is what an injury actually
    does to that club's team sheet.

    Args:
        players: Canonical player frame.
        settings: Supplies the references to exclude.

    Returns:
        The frame without the excluded players.

    Raises:
        ValueError: If any reference matches no player or several.
    """
    if not settings.excluded_players:
        return players
    excluded = {
        resolve_player(players, reference) for reference in sorted(settings.excluded_players)
    }
    return players.filter(~pl.col("player_id").is_in(excluded))


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


def season_files(data_dir: Path) -> list[Path]:
    """Return every per-season payload under ``data_dir``, oldest first.

    Args:
        data_dir: Directory holding the exports; the payloads live in its ``json`` subdirectory.

    Returns:
        Payload paths in season order.

    Raises:
        FileNotFoundError: If the subdirectory holds no parseable payload.
    """
    season_dir = data_dir / SEASON_DIR_NAME
    payloads = sorted(
        path for path in season_dir.glob("*.json") if SEASON_FILENAME_PATTERN.match(path.name)
    )
    if not payloads:
        raise FileNotFoundError(f"no YYYY.json season payload found in {season_dir}")
    return payloads


def load_season(path: Path) -> pl.DataFrame:
    """Read one season payload into the canonical player frame.

    Args:
        path: Path to a ``YYYY.json`` payload.

    Returns:
        The canonical player columns plus ``season``, ``starts``, ``sub_appearances`` and
        ``appearances``. Players carrying the :data:`UNPURCHASABLE_MARKET_VALUE` sentinel are
        dropped, as in :func:`load_players`; they could not be bought that season, and the
        appearance data confirms it — 71 of the 72 such rows never featured.

    Raises:
        ValueError: If the payload is missing a section, a player's points do not equal the sum
            of the scoring channels they decompose into, a position is unknown, or a player is
            credited with more appearances than the season has matchdays.
    """
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf8"))

    missing = [section for section in ("players", "teams", "rounds") if section not in payload]
    if missing:
        raise ValueError(f"{path.name} is missing expected sections: {missing}")

    club_names = {team["id"]: team["name"] for team in payload["teams"]}
    matchdays = len(payload["rounds"])

    records = []
    for player in payload["players"]:
        breakdown = player["ratingBreakDown"]
        components = sum(breakdown[channel] for channel in BREAKDOWN_COMPONENTS)
        if components != breakdown["ratingSum"] or breakdown["ratingSum"] != player["rating"]:
            raise ValueError(
                f"{path.name}: {player['id']} scores {player['rating']} but its channels sum to "
                f"{components}; the breakdown and the total describe different things"
            )
        records.append(
            {
                "player_id": player["id"],
                "name": player["displayLongName"],
                "club": club_names[player["teamId"]],
                "position": player["position"],
                "market_value": player["marketValue"],
                "points": player["rating"],
                "grade_average": breakdown["averageGrade"] / 100,
                "starts": breakdown["starter"],
                "sub_appearances": breakdown["joker"],
            }
        )

    players = pl.DataFrame(records)
    unknown = set(players.get_column("position").unique()) - {
        position.value for position in Position
    }
    if unknown:
        raise ValueError(f"{path.name} contains unknown positions: {sorted(unknown)}")

    players = (
        players.with_columns(
            pl.col("position").cast(POSITION_DTYPE),
            pl.col("market_value").cast(pl.Int64),
            pl.col("points").cast(pl.Int64),
            pl.col("grade_average").cast(pl.Float64),
            pl.lit(season_of(path), dtype=pl.Int32).alias("season"),
            (pl.col("starts") + pl.col("sub_appearances")).alias("appearances"),
        )
        .filter(pl.col("market_value") != UNPURCHASABLE_MARKET_VALUE)
        .select(
            "player_id",
            "name",
            "club",
            "position",
            "market_value",
            "points",
            "grade_average",
            "season",
            "starts",
            "sub_appearances",
            "appearances",
        )
    )

    over_played = players.filter(pl.col("appearances") > matchdays)
    if over_played.height:
        raise ValueError(
            f"{path.name} credits {over_played.height} players with more than the {matchdays} "
            f"matchdays the season holds"
        )

    _reject_invalid_rows(players, path.name)
    return players


def season_of(path: Path) -> int:
    """Return the season a payload describes, as the year in its filename.

    Args:
        path: Path to a ``YYYY.json`` payload.

    Returns:
        The season the squads were assembled for and the points were scored in.

    Raises:
        ValueError: If the filename is not a bare four-digit year.
    """
    match = SEASON_FILENAME_PATTERN.match(path.name)
    if match is None:
        raise ValueError(f"{path.name!r} is not a YYYY.json season payload")
    return int(match.group(1))


def load_panel(data_dir: Path) -> pl.DataFrame:
    """Load every completed season into one frame.

    This is the fitting sample: each row pairs the price a player carried for a season with the
    points he went on to score *in that same season*, and adds the appearances behind that total.

    Args:
        data_dir: Directory holding the exports; the payloads live in its ``json`` subdirectory.

    Returns:
        The canonical player columns plus ``season``, ``starts``, ``sub_appearances`` and
        ``appearances``.

    Raises:
        FileNotFoundError: If no season payload is present.
        ValueError: If a payload fails validation.
    """
    return pl.concat(load_season(path) for path in season_files(data_dir))


def load_latest_players(settings: Settings) -> pl.DataFrame:
    """Load the newest export, drop any excluded players, and validate what remains.

    Args:
        settings: Supplies the data directory, the exclusions, and the rules to validate against.

    Returns:
        The validated canonical player frame, ready to project and solve against.

    Raises:
        ValueError: If an exclusion does not resolve, or the surviving pool cannot field a legal
            squad.
    """
    players = apply_exclusions(load_players(latest_export(settings.data_dir)), settings)
    validate_pool(players, settings)
    return players
