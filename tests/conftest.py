"""Fixtures building synthetic kicker exports and season payloads on disk."""

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPORT_HEADER = (
    "ID;Vorname;Nachname;Angezeigter Name (kurz);Angezeigter Name;"
    "Verein;Position;Marktwert;Punkte;Notendurchschnitt"
)

POOL_SIZES = {"GOALKEEPER": 4, "DEFENDER": 8, "MIDFIELDER": 8, "FORWARD": 5}
"""Comfortably above the squad quota, spread over enough clubs to respect the club cap."""


def export_row(
    index: int,
    club: str,
    position: str,
    market_value: int,
    points: int = 0,
    grade_average: float = 0.0,
) -> str:
    """Render one export line.

    Args:
        index: Unique player number, used to build the id and display names.
        club: Club name.
        position: One of the four kicker position values.
        market_value: Market value in euros.
        points: Season points total.
        grade_average: Average kicker grade.

    Returns:
        A semicolon-separated export line.
    """
    return (
        f"pl-k{index:05d};Vorname{index};Nachname{index};N{index};Name {index};"
        f"{club};{position};{market_value};{points};{grade_average}"
    )


def write_export(directory: Path, stamp: str, rows: list[str]) -> Path:
    """Write an export file with the given rows.

    Args:
        directory: Directory to write into.
        stamp: ``YYYY_MM_DD`` date stamp for the filename.
        rows: Already-rendered export lines.

    Returns:
        Path to the written file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}_spieler_daten.csv"
    path.write_text("\n".join([EXPORT_HEADER, *rows]) + "\n", encoding="utf8")
    return path


def build_rows(market_value: int = 1_000_000, clubs: int = 6) -> list[str]:
    """Build a pool that satisfies the default quotas.

    Args:
        market_value: Market value given to every player.
        clubs: Number of distinct clubs to spread the players across.

    Returns:
        Export lines for a legal pool.
    """
    rows = []
    index = 0
    for position, count in POOL_SIZES.items():
        for _ in range(count):
            rows.append(export_row(index, f"Club {index % clubs}", position, market_value))
            index += 1
    return rows


def season_player(
    index: int,
    club: str,
    position: str,
    market_value: int,
    starts: int = 0,
    subs: int = 0,
    grade_points: int = 0,
    goal_points: int = 0,
    grade_average: float = 0.0,
) -> dict[str, Any]:
    """Render one player of a season payload, with a self-consistent breakdown.

    The scoring channels are built to sum to the total, because the loader rejects a payload
    where they do not — a test player has to be as internally consistent as a real one.

    Args:
        index: Unique player number, used to build the id and display name.
        club: Club id the player belongs to.
        position: One of the four kicker position values.
        market_value: Market value in euros.
        starts: Matches started, worth 4 points each.
        subs: Matches come on as a substitute, worth 2 points each.
        grade_points: Points earned from grades, which may be negative.
        goal_points: Points earned from goals.
        grade_average: Mean kicker grade; 0.0 means never graded.

    Returns:
        One entry of the payload's ``players`` list.
    """
    channels = {
        "ratingGrade": grade_points,
        "ratingGoals": goal_points,
        "ratingCards": 0,
        "ratingAssists": 0,
        "ratingStarter": 4 * starts,
        "ratingMvp": 0,
        "ratingCleanSheet": 0,
        "ratingJoker": 2 * subs,
    }
    total = sum(channels.values())
    return {
        "id": f"pl-k{index:05d}",
        "teamId": club,
        "displayLongName": f"Name {index}",
        "marketValue": market_value,
        "position": position,
        "rating": total,
        "ratingBreakDown": {
            **channels,
            "averageGrade": round(grade_average * 100),
            "starter": starts,
            "joker": subs,
            "ratingSum": total,
        },
    }


def write_season(
    directory: Path,
    season: int,
    players: list[dict[str, Any]],
    matchdays: int = 34,
) -> Path:
    """Write a season payload under the ``json`` subdirectory.

    Args:
        directory: Data directory; the payload lands in its ``json`` subdirectory.
        season: Year the payload describes, which becomes its filename.
        players: Entries from :func:`season_player`.
        matchdays: Number of rounds the season holds.

    Returns:
        Path to the written payload.
    """
    clubs = sorted({player["teamId"] for player in players})
    payload = {
        "players": players,
        "teams": [{"id": club, "name": club.replace("tm-", "Club ")} for club in clubs],
        "rounds": [{"id": f"rn-{day}"} for day in range(matchdays)],
        "matches": [],
    }
    season_dir = directory / "json"
    season_dir.mkdir(parents=True, exist_ok=True)
    path = season_dir / f"{season}.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    return path


def build_season_players(
    market_value: int = 1_000_000, clubs: int = 6, starts: int = 20
) -> list[dict[str, Any]]:
    """Build a payload roster satisfying the default quotas.

    Args:
        market_value: Market value given to every player.
        clubs: Number of distinct clubs to spread the players across.
        starts: Matches started by every player.

    Returns:
        Entries for a legal season payload.
    """
    players = []
    index = 0
    for position, count in POOL_SIZES.items():
        for _ in range(count):
            players.append(
                season_player(index, f"tm-{index % clubs}", position, market_value, starts=starts)
            )
            index += 1
    return players


@pytest.fixture
def sample_export(tmp_path: Path) -> Path:
    """Write a single valid export and return its path.

    Args:
        tmp_path: pytest-provided temporary directory.

    Returns:
        Path to the written export.
    """
    return write_export(tmp_path, "2026_08_09", build_rows())
