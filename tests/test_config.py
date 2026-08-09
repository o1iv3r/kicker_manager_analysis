"""Tests for the settings model and its quota validation."""

import pytest
from pydantic import ValidationError

from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.scoring import Position


def test_defaults_describe_the_bundesliga_rules() -> None:
    """The shipped defaults are the 15-man squad, 4-4-2 and 30M budget of the Bundesliga."""
    settings = Settings()
    assert settings.budget == 30_000_000
    assert settings.club_cap == 3
    assert settings.squad_size == 15
    assert settings.lineup_size == 11


def test_bench_defaults_to_one_player_per_position() -> None:
    """The 2/5/5/3 squad minus a 4-4-2 lineup leaves exactly one substitute per position."""
    assert Settings().bench_quota == dict.fromkeys(Position, 1)


def test_bench_is_worthless_by_default() -> None:
    """Bench players are modelled as scoring nothing unless that is explicitly overridden."""
    assert Settings().bench_weight == 0.0


def test_lineup_may_not_exceed_the_squad() -> None:
    """Fielding more players in a position than were bought is not a legal squad."""
    with pytest.raises(ValidationError, match="lineup_quota exceeds squad_quota"):
        Settings(squad_quota={**dict(Settings().squad_quota), Position.FORWARD: 1})


def test_quotas_must_cover_every_position() -> None:
    """A quota missing a position would silently drop that position from the squad."""
    partial = {Position.GOALKEEPER: 2, Position.DEFENDER: 5}
    with pytest.raises(ValidationError, match="missing positions"):
        Settings(squad_quota=partial)


def test_negative_quotas_are_rejected() -> None:
    """A negative quota is nonsense the optimizer must never be handed."""
    quota = {**dict(Settings().squad_quota), Position.DEFENDER: -1}
    with pytest.raises(ValidationError, match="negative counts"):
        Settings(squad_quota=quota)


def test_budget_must_be_positive() -> None:
    """A non-positive budget cannot buy a squad."""
    with pytest.raises(ValidationError):
        Settings(budget=0)


def test_settings_are_frozen() -> None:
    """Settings are immutable so a run cannot change the rules midway through."""
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.budget = 1_000_000  # type: ignore[misc]
