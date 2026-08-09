"""Configuration for squad optimization.

Defaults encode the Bundesliga variant of the kicker-Managerspiel Classic. Every value can be
overridden through ``KICKER_``-prefixed environment variables.
"""

from pathlib import Path
from typing import Final, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kicker_manager_analysis.scoring import Position

BUNDESLIGA_BUDGET: Final = 30_000_000
"""Squad budget in euros. The 2. Bundesliga plays for 7.5M and the 3. Liga for 4M."""

SQUAD_QUOTA: Final[dict[Position, int]] = {
    Position.GOALKEEPER: 2,
    Position.DEFENDER: 5,
    Position.MIDFIELDER: 5,
    Position.FORWARD: 3,
}
"""Mandatory composition of the 15-man squad."""

LINEUP_QUOTA: Final[dict[Position, int]] = {
    Position.GOALKEEPER: 1,
    Position.DEFENDER: 4,
    Position.MIDFIELDER: 4,
    Position.FORWARD: 2,
}
"""The fixed 4-4-2 formation; the difference to the squad quota is the bench."""


class Settings(BaseSettings):
    """Rules and paths that define one optimization run.

    The quotas are configurable so that the optimizer can be exercised against small,
    brute-forceable pools in tests, not because the game offers a choice.
    """

    model_config = SettingsConfigDict(env_prefix="KICKER_", frozen=True)

    data_dir: Path = Path("data")
    budget: int = Field(default=BUNDESLIGA_BUDGET, gt=0)
    club_cap: int = Field(default=3, gt=0)
    excluded_players: frozenset[str] = frozenset()
    """Players to drop from the pool before anything is fitted against it or solved.

    For news the data cannot know: a season-ending injury, a transfer out of the league, a
    suspension. Each entry is either a ``player_id`` or a case-insensitive part of a name, and an
    entry matching no player — or more than one — is an error rather than a silent no-op, since
    the whole point is to be sure the player cannot be bought.

    Excluding a club's first-choice goalkeeper promotes his deputy to price rank 1, which is the
    right behaviour for the injury case that motivates this field. See
    :func:`~.data.apply_exclusions`.
    """
    bench_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    residual_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    """Override for how far last season is trusted over the market's forecast of the next one.

    Left unset, the weight is *measured* from the panel per position — the panel exists precisely
    so this does not have to be guessed. Set it only to explore how the answer moves.
    """
    squad_quota: dict[Position, int] = Field(default_factory=lambda: dict(SQUAD_QUOTA))
    lineup_quota: dict[Position, int] = Field(default_factory=lambda: dict(LINEUP_QUOTA))

    @model_validator(mode="after")
    def _check_quotas(self) -> Self:
        """Reject quotas that could never describe a legal squad.

        Returns:
            The validated settings.

        Raises:
            ValueError: If a position is missing, a count is negative, or the lineup asks for
                more players in a position than the squad contains.
        """
        for name, quota in (("squad_quota", self.squad_quota), ("lineup_quota", self.lineup_quota)):
            missing = set(Position) - set(quota)
            if missing:
                raise ValueError(f"{name} is missing positions: {sorted(missing)}")
            negative = sorted(pos for pos, count in quota.items() if count < 0)
            if negative:
                raise ValueError(f"{name} has negative counts for: {negative}")

        overfilled = sorted(
            pos for pos in Position if self.lineup_quota[pos] > self.squad_quota[pos]
        )
        if overfilled:
            raise ValueError(f"lineup_quota exceeds squad_quota for: {overfilled}")
        return self

    @property
    def squad_size(self) -> int:
        """Total number of players that must be bought."""
        return sum(self.squad_quota.values())

    @property
    def lineup_size(self) -> int:
        """Total number of players that score points."""
        return sum(self.lineup_quota.values())

    @property
    def bench_quota(self) -> dict[Position, int]:
        """Number of non-scoring squad players required per position."""
        return {pos: self.squad_quota[pos] - self.lineup_quota[pos] for pos in Position}
