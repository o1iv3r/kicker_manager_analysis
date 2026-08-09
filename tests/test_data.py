"""Tests for export discovery, loading and pool validation."""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from conftest import REPO_ROOT, build_rows, export_row, write_export
from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.data import (
    export_date,
    latest_export,
    load_players,
    minimum_squad_cost,
    validate_pool,
)
from kicker_manager_analysis.scoring import Position


def test_export_date_reads_the_stamp(sample_export: Path) -> None:
    """The download date is recovered from the filename."""
    assert export_date(sample_export) == date(2026, 8, 9)


def test_export_date_rejects_unstamped_names(tmp_path: Path) -> None:
    """A file without a date stamp cannot be ordered against the others."""
    with pytest.raises(ValueError, match="is not a"):
        export_date(tmp_path / "spieler_daten.csv")


def test_latest_export_prefers_the_newest_stamp(tmp_path: Path) -> None:
    """Discovery is by date, not by filesystem order or modification time."""
    write_export(tmp_path, "2026_08_09", build_rows())
    newest = write_export(tmp_path, "2026_08_21", build_rows())
    write_export(tmp_path, "2025_12_31", build_rows())
    assert latest_export(tmp_path) == newest


def test_latest_export_ignores_unrelated_csvs(tmp_path: Path) -> None:
    """Other CSVs living in the data directory are not mistaken for exports."""
    expected = write_export(tmp_path, "2026_08_09", build_rows())
    (tmp_path / "appearances.csv").write_text("a;b\n1;2\n", encoding="utf8")
    assert latest_export(tmp_path) == expected


def test_latest_export_raises_on_empty_directory(tmp_path: Path) -> None:
    """A missing export is reported plainly rather than surfacing later as a schema error."""
    with pytest.raises(FileNotFoundError, match=r"no .* export found"):
        latest_export(tmp_path)


def test_load_players_returns_canonical_columns(sample_export: Path) -> None:
    """German source columns are renamed and the unused name columns dropped."""
    players = load_players(sample_export)
    assert players.columns == [
        "player_id",
        "name",
        "club",
        "position",
        "market_value",
        "points",
        "grade_average",
    ]
    assert players.height == 25


def test_load_players_types_the_position_as_an_enum(sample_export: Path) -> None:
    """An enum dtype makes an unrecognised position impossible downstream."""
    dtype = load_players(sample_export).schema["position"]
    assert isinstance(dtype, pl.Enum)
    assert set(dtype.categories) == {position.value for position in Position}


def test_load_players_rejects_missing_columns(tmp_path: Path) -> None:
    """A changed export layout must fail loudly rather than yield a half-empty frame."""
    path = tmp_path / "2026_08_09_spieler_daten.csv"
    path.write_text("ID;Verein\npl-k00001;Club 0\n", encoding="utf8")
    with pytest.raises(ValueError, match="missing expected columns"):
        load_players(path)


def test_load_players_rejects_unknown_positions(tmp_path: Path) -> None:
    """A position the game does not use would break the quota constraints."""
    rows = [*build_rows(), export_row(99, "Club 0", "SWEEPER", 500_000)]
    path = write_export(tmp_path, "2026_08_09", rows)
    with pytest.raises(ValueError, match="unknown positions"):
        load_players(path)


def test_load_players_rejects_duplicate_ids(tmp_path: Path) -> None:
    """A duplicated id would let the optimizer field the same player twice."""
    rows = build_rows()
    path = write_export(tmp_path, "2026_08_09", [*rows, rows[0]])
    with pytest.raises(ValueError, match="duplicated player ids"):
        load_players(path)


def test_load_players_rejects_non_positive_market_values(tmp_path: Path) -> None:
    """A free player would let the budget constraint be gamed."""
    rows = [*build_rows(), export_row(99, "Club 0", "FORWARD", 0)]
    path = write_export(tmp_path, "2026_08_09", rows)
    with pytest.raises(ValueError, match="non-positive market value"):
        load_players(path)


def test_validate_pool_accepts_a_legal_pool(sample_export: Path) -> None:
    """The synthetic pool satisfies every necessary condition."""
    validate_pool(load_players(sample_export), Settings())


def test_validate_pool_rejects_an_under_supplied_position(tmp_path: Path) -> None:
    """Two goalkeepers are required, so a pool holding one cannot produce a squad."""
    rows = [row for row in build_rows() if "GOALKEEPER" not in row]
    rows.append(export_row(90, "Club 0", "GOALKEEPER", 500_000))
    path = write_export(tmp_path, "2026_08_09", rows)
    with pytest.raises(ValueError, match="cannot fill the squad quota"):
        validate_pool(load_players(path), Settings())


def test_validate_pool_rejects_too_few_clubs(tmp_path: Path) -> None:
    """With a cap of three per club, a 15-man squad needs at least five clubs."""
    path = write_export(tmp_path, "2026_08_09", build_rows(clubs=4))
    with pytest.raises(ValueError, match="at least 5"):
        validate_pool(load_players(path), Settings())


def test_validate_pool_rejects_an_unaffordable_pool(sample_export: Path) -> None:
    """If even the cheapest legal squad busts the budget, no solve can succeed."""
    with pytest.raises(ValueError, match="cheapest possible squad"):
        validate_pool(load_players(sample_export), Settings(budget=1_000_000))


def test_minimum_squad_cost_sums_the_cheapest_of_each_position(tmp_path: Path) -> None:
    """The bound takes the quota-many cheapest players in every position."""
    rows = [
        *build_rows(market_value=2_000_000),
        *[export_row(50 + n, f"Club {n}", "DEFENDER", 500_000) for n in range(5)],
    ]
    path = write_export(tmp_path, "2026_08_09", rows)
    settings = Settings()
    # 5 defenders at 500k, the remaining 10 squad slots at 2M.
    assert minimum_squad_cost(load_players(path), settings) == 5 * 500_000 + 10 * 2_000_000


def test_real_export_loads_and_validates() -> None:
    """The export actually committed to the repo passes every check."""
    settings = Settings(data_dir=REPO_ROOT / "data")
    players = load_players(latest_export(settings.data_dir))
    validate_pool(players, settings)
    assert players.height > 0
