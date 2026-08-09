"""Tests for export discovery, loading and pool validation."""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from conftest import (
    REPO_ROOT,
    build_rows,
    build_season_players,
    export_row,
    season_player,
    write_export,
    write_season,
)
from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.data import (
    MAX_PLAUSIBLE_MARKET_VALUE,
    UNPURCHASABLE_MARKET_VALUE,
    all_exports,
    apply_exclusions,
    export_date,
    latest_export,
    load_latest_players,
    load_panel,
    load_players,
    load_season,
    minimum_squad_cost,
    season_files,
    season_of,
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


def test_export_date_accepts_a_year_only_stamp(tmp_path: Path) -> None:
    """The historical exports carry only a year, and must not be rejected as unparseable."""
    assert export_date(tmp_path / "2023_spieler_daten.csv") == date(2023, 1, 1)


def test_all_exports_orders_mixed_stamp_forms(tmp_path: Path) -> None:
    """A year-only export sorts ahead of a dated one from the same year."""
    write_export(tmp_path, "2026_08_09", build_rows())
    write_export(tmp_path, "2024", build_rows())
    write_export(tmp_path, "2026", build_rows())
    assert [path.name for path in all_exports(tmp_path)] == [
        "2024_spieler_daten.csv",
        "2026_spieler_daten.csv",
        "2026_08_09_spieler_daten.csv",
    ]


def test_load_players_drops_the_unpurchasable_sentinel(tmp_path: Path) -> None:
    """999M is a marker for a player who cannot be bought, not a valuation."""
    rows = [
        *build_rows(),
        export_row(90, "Club 0", "FORWARD", UNPURCHASABLE_MARKET_VALUE),
        export_row(91, "Club 1", "GOALKEEPER", UNPURCHASABLE_MARKET_VALUE),
    ]
    players = load_players(write_export(tmp_path, "2026_08_09", rows))
    assert players.height == 25
    assert players.get_column("market_value").to_numpy().max() < MAX_PLAUSIBLE_MARKET_VALUE


def test_load_players_rejects_an_unrecognised_sentinel(tmp_path: Path) -> None:
    """A different out-of-range marker must fail loudly rather than skew a regression."""
    rows = [*build_rows(), export_row(90, "Club 0", "FORWARD", 888_000_000)]
    with pytest.raises(ValueError, match="unhandled sentinel"):
        load_players(write_export(tmp_path, "2026_08_09", rows))


def test_exclusions_default_to_nothing(sample_export: Path) -> None:
    """An unset exclusion list must leave the pool untouched."""
    players = load_players(sample_export)
    assert apply_exclusions(players, Settings()).height == players.height


def test_exclusion_by_id_and_by_name(sample_export: Path) -> None:
    """Either form identifies a player; the name form is case-insensitive."""
    players = load_players(sample_export)
    by_id = apply_exclusions(players, Settings(excluded_players={"pl-k00000"}))
    by_name = apply_exclusions(players, Settings(excluded_players={"name 0"}))
    assert "pl-k00000" not in by_id.get_column("player_id").to_list()
    assert by_id.height == players.height - 1
    assert by_name.get_column("player_id").to_list() == by_id.get_column("player_id").to_list()


def test_exclusion_matching_nothing_raises(sample_export: Path) -> None:
    """The dangerous failure is a silent no-op: the caller thinks the player is gone."""
    with pytest.raises(ValueError, match="matches no player"):
        apply_exclusions(load_players(sample_export), Settings(excluded_players={"Lewandowski"}))


def test_ambiguous_exclusion_raises_and_names_candidates(sample_export: Path) -> None:
    """'Name 1' also matches 'Name 10'; guessing between them would be worse than stopping."""
    with pytest.raises(ValueError, match=r"matches \d+ players"):
        apply_exclusions(load_players(sample_export), Settings(excluded_players={"Name 1"}))


def test_exclusions_are_validated_against_the_squad_quota(tmp_path: Path) -> None:
    """Excluding too many of a position must fail as validation, not as an infeasible solve."""
    path = write_export(tmp_path, "2026_08_09", build_rows())
    settings = Settings(data_dir=tmp_path, excluded_players={f"pl-k{n:05d}" for n in range(3)})
    with pytest.raises(ValueError, match="cannot fill the squad quota"):
        load_latest_players(settings)
    assert path.exists()


def test_season_of_reads_the_year(tmp_path: Path) -> None:
    """A payload is keyed by the season in its filename."""
    assert season_of(tmp_path / "2023.json") == 2023
    with pytest.raises(ValueError, match="is not a"):
        season_of(tmp_path / "2023_08_09.json")


def test_season_files_orders_by_season(tmp_path: Path) -> None:
    """Payloads are discovered under ``json/`` and returned oldest first."""
    for season in (2025, 2023, 2024):
        write_season(tmp_path, season, build_season_players())
    assert [path.stem for path in season_files(tmp_path)] == ["2023", "2024", "2025"]


def test_season_files_requires_a_payload(tmp_path: Path) -> None:
    """An empty data directory must say so rather than yield an empty panel."""
    with pytest.raises(FileNotFoundError, match=r"no YYYY\.json"):
        season_files(tmp_path)


def test_load_season_derives_appearances(tmp_path: Path) -> None:
    """Appearances are starts plus substitute appearances, the denominator the CSV lacks."""
    players = [
        *build_season_players(),
        season_player(90, "tm-0", "MIDFIELDER", 1_000_000, starts=12, subs=5, grade_points=8),
    ]
    season = load_season(write_season(tmp_path, 2024, players))
    row = season.filter(pl.col("player_id") == "pl-k00090").row(0, named=True)
    assert (row["starts"], row["sub_appearances"], row["appearances"]) == (12, 5, 17)
    assert row["points"] == 4 * 12 + 2 * 5 + 8
    assert season.get_column("season").unique().to_list() == [2024]


def test_load_season_rejects_a_broken_decomposition(tmp_path: Path) -> None:
    """If the channels do not sum to the total, the appearance counts cannot be trusted."""
    broken = season_player(90, "tm-0", "MIDFIELDER", 1_000_000, starts=10)
    broken["rating"] = 999
    with pytest.raises(ValueError, match="channels sum to"):
        load_season(write_season(tmp_path, 2024, [*build_season_players(), broken]))


def test_load_season_rejects_more_appearances_than_matchdays(tmp_path: Path) -> None:
    """A player cannot feature more often than the season has rounds."""
    too_many = season_player(90, "tm-0", "MIDFIELDER", 1_000_000, starts=30, subs=8)
    with pytest.raises(ValueError, match="more than the 34 matchdays"):
        load_season(write_season(tmp_path, 2024, [*build_season_players(), too_many]))


def test_load_season_drops_the_unpurchasable_sentinel(tmp_path: Path) -> None:
    """The sentinel is filtered from the panel exactly as it is from the pool."""
    unbuyable = season_player(90, "tm-0", "FORWARD", UNPURCHASABLE_MARKET_VALUE)
    season = load_season(write_season(tmp_path, 2024, [*build_season_players(), unbuyable]))
    assert season.height == 25
    assert season.get_column("market_value").to_numpy().max() <= MAX_PLAUSIBLE_MARKET_VALUE


def test_load_panel_stacks_every_season(tmp_path: Path) -> None:
    """Each payload pairs a season's prices with that same season's points, so all of them fit."""
    write_season(tmp_path, 2024, build_season_players())
    write_season(tmp_path, 2025, build_season_players(market_value=1_500_000))
    panel = load_panel(tmp_path)
    assert panel.get_column("season").unique().sort().to_list() == [2024, 2025]
    assert panel.height == 50


def test_real_panel_covers_three_seasons() -> None:
    """The committed payloads yield three seasons of correctly paired prices and points."""
    panel = load_panel(REPO_ROOT / "data")
    assert panel.get_column("season").unique().sort().to_list() == [2023, 2024, 2025]
    assert panel.height == 1380
    assert panel.get_column("market_value").to_numpy().max() <= MAX_PLAUSIBLE_MARKET_VALUE
    assert panel.get_column("appearances").to_numpy().max() <= 34


def test_real_panel_reconciles_with_the_csv_exports() -> None:
    """The two sources must agree exactly, or they describe different seasons.

    Cheap to check and strong: the payloads are only usable as the fitting sample because they
    carry the same prices and points the CSVs do. Drift here means one of them was re-exported.
    """
    panel = load_panel(REPO_ROOT / "data")
    for season in (2023, 2024, 2025):
        csv = load_players(REPO_ROOT / "data" / f"{season}_spieler_daten.csv")
        merged = panel.filter(pl.col("season") == season).join(
            csv, on="player_id", suffix="_csv", how="inner"
        )
        assert merged.height == csv.height
        for column in ("market_value", "points", "grade_average"):
            assert merged.get_column(column).equals(
                merged.get_column(f"{column}_csv"), check_names=False
            ), f"{season}: {column} differs between the payload and the export"
