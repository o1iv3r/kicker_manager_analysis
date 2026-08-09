"""Fixtures building synthetic kicker exports on disk."""

from pathlib import Path

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


@pytest.fixture
def sample_export(tmp_path: Path) -> Path:
    """Write a single valid export and return its path.

    Args:
        tmp_path: pytest-provided temporary directory.

    Returns:
        Path to the written export.
    """
    return write_export(tmp_path, "2026_08_09", build_rows())
