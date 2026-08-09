"""Tests for the squad integer programme."""

import random
from collections import Counter
from itertools import combinations, product

import polars as pl
import pytest

from conftest import REPO_ROOT
from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.data import POSITION_DTYPE, load_latest_players, load_panel
from kicker_manager_analysis.optimize import Squad, optimize, optimize_top_k, squad_frame
from kicker_manager_analysis.projection import fit_and_project
from kicker_manager_analysis.scoring import Position

SMALL_POOL_SIZES = {
    Position.GOALKEEPER: 4,
    Position.DEFENDER: 6,
    Position.MIDFIELDER: 6,
    Position.FORWARD: 4,
}
"""Twenty players: 864 quota-satisfying squads, which brute force can enumerate exactly."""

SMALL_POOL_CLUBS = 6
"""Enough clubs that the cap binds without dictating the answer — six clubs allow 18 of the 15."""

SMALL_POOL_BUDGET = 34_000_000
"""Between the cheapest legal squad at 28.9M and the dearest at 41.9M, so the budget binds."""


def small_pool(seed: int = 7) -> pl.DataFrame:
    """Build a pool small enough to brute-force and irregular enough to have one clear optimum.

    Args:
        seed: Seed for the pseudo-random prices and points, fixed so the test is deterministic.

    Returns:
        A projected pool carrying the columns the programme reads.
    """
    generator = random.Random(seed)
    rows = []
    index = 0
    for position, count in SMALL_POOL_SIZES.items():
        for _ in range(count):
            rows.append(
                {
                    "player_id": f"pl-{index:02d}",
                    "position": position.value,
                    "club": f"Club {index % SMALL_POOL_CLUBS}",
                    "market_value": generator.randrange(5, 45) * 100_000,
                    "projected_points": round(generator.uniform(0.0, 200.0), 3),
                }
            )
            index += 1
    return pl.DataFrame(rows, schema_overrides={"position": POSITION_DTYPE})


def brute_force(pool: pl.DataFrame, settings: Settings) -> tuple[frozenset[str], float, int]:
    """Enumerate every legal squad and return the best one under the same lexicographic rule.

    The best lineup within a squad needs no search: the only constraints on ``s_p`` are
    ``s_p <= x_p`` and the per-position quota, so the top scorers of each position are optimal.

    Args:
        pool: A projected pool, as built by :func:`small_pool`.
        settings: Rules to enumerate under.

    Returns:
        The optimal squad's player ids, its lineup points and its cost.

    Raises:
        AssertionError: If no legal squad exists, or if more than one attains the optimum — the
            comparison against CBC is only meaningful when the answer is unique.
    """
    by_position = {
        position: pool.filter(pl.col("position") == position.value)
        .select("player_id", "club", "market_value", "projected_points")
        .rows()
        for position in Position
    }

    best: tuple[float, int] | None = None
    winners: list[frozenset[str]] = []
    for selection in product(
        *(
            combinations(by_position[position], settings.squad_quota[position])
            for position in Position
        )
    ):
        players = [player for group in selection for player in group]
        cost: int = sum(player[2] for player in players)
        if cost > settings.budget:
            continue
        if max(Counter(player[1] for player in players).values()) > settings.club_cap:
            continue

        lineup_points: float = sum(
            sum(
                sorted((player[3] for player in group), reverse=True)[
                    : settings.lineup_quota[position]
                ]
            )
            for position, group in zip(Position, selection, strict=True)
        )
        score = (lineup_points, -cost)
        if best is None or score > best:
            best = score
            winners = [frozenset(player[0] for player in players)]
        elif score == best:
            winners.append(frozenset(player[0] for player in players))

    assert best is not None, "the brute-force pool admits no legal squad"
    assert len(winners) == 1, f"{len(winners)} squads tie for the optimum; the test cannot compare"
    return winners[0], best[0], -best[1]


@pytest.fixture(scope="module")
def real_pool() -> pl.DataFrame:
    """Project the committed 2026/27 pool once for every test that needs it.

    Returns:
        The pool with expected points attached.
    """
    settings = Settings(data_dir=REPO_ROOT / "data")
    projected, _ = fit_and_project(
        load_panel(settings.data_dir), load_latest_players(settings), settings
    )
    return projected


def test_matches_brute_force_on_a_small_pool() -> None:
    """The solver must return the squad an exhaustive search finds, not merely a good one.

    Enumeration is the only check that can distinguish a correct model from a plausible one, and
    it covers the tie-break too: the brute force applies the same rule, best points first and
    cheapest among those.
    """
    pool = small_pool()
    settings = Settings(budget=SMALL_POOL_BUDGET)
    expected, expected_points, expected_cost = brute_force(pool, settings)

    squad = optimize(pool, settings)
    assert frozenset(squad.players) == expected
    assert squad.lineup_points == pytest.approx(expected_points)
    assert squad.cost == expected_cost


def test_the_small_pool_actually_constrains_the_answer() -> None:
    """A test pool whose constraints never bind would prove nothing about the constraints."""
    pool = small_pool()
    settings = Settings(budget=SMALL_POOL_BUDGET)
    squad = optimize(pool, settings)
    frame = squad_frame(pool, squad)

    assert squad.cost > settings.budget * 0.9
    assert frame.group_by("club").len().get_column("len").max() == settings.club_cap


def test_alternatives_are_distinct_squads() -> None:
    """No-good cuts must exclude the squad already found, not merely re-rank the same one."""
    pool = small_pool()
    settings = Settings(budget=SMALL_POOL_BUDGET)
    squads = optimize_top_k(pool, settings, 5)

    assert len(squads) == 5
    assert len({frozenset(squad.players) for squad in squads}) == 5
    points = [squad.lineup_points for squad in squads]
    assert points == sorted(points, reverse=True)


def test_enumeration_stops_when_the_pool_runs_out() -> None:
    """Asking for more squads than exist returns the ones that do rather than failing."""
    pool = small_pool()
    # One legal squad per position choice, so the quota is the pool and there is nothing to vary.
    settings = Settings(
        budget=10**9,
        club_cap=15,
        squad_quota=dict(SMALL_POOL_SIZES),
        lineup_quota={
            Position.GOALKEEPER: 1,
            Position.DEFENDER: 4,
            Position.MIDFIELDER: 4,
            Position.FORWARD: 2,
        },
    )
    assert len(optimize_top_k(pool, settings, 3)) == 1


def test_bench_weight_changes_what_the_bench_is_for() -> None:
    """At weight zero the bench is bought as cheaply as possible; above it, it is bought to score.

    This is the check that the weight reaches the objective at all, rather than being carried
    through settings and quietly ignored.
    """
    pool = small_pool()
    cheap = optimize(pool, Settings(budget=SMALL_POOL_BUDGET))
    valued = optimize(pool, Settings(budget=SMALL_POOL_BUDGET, bench_weight=1.0))

    cheap_bench = squad_frame(pool, cheap).filter(~pl.col("in_lineup"))
    valued_bench = squad_frame(pool, valued).filter(~pl.col("in_lineup"))
    assert valued.bench_points > cheap.bench_points
    assert valued_bench.get_column("market_value").sum() > (
        cheap_bench.get_column("market_value").sum()
    )
    # Whatever the weight buys the bench, it is paid for out of the lineup: the zero-weight solve
    # is by construction the one that maximises lineup points.
    assert valued.lineup_points <= cheap.lineup_points


def test_a_pool_that_cannot_field_a_squad_is_rejected() -> None:
    """Infeasibility must name the constraints rather than surface as an empty result."""
    with pytest.raises(ValueError, match="no squad of 15"):
        optimize(small_pool(), Settings(budget=1_000_000))


def test_a_pool_missing_the_projection_is_rejected() -> None:
    """Solving against a pool that was never projected would silently pick on price alone."""
    with pytest.raises(ValueError, match="missing columns"):
        optimize(small_pool().drop("projected_points"), Settings())


def test_a_null_projection_is_rejected() -> None:
    """A null coefficient would fail inside PuLP with an error naming no player."""
    pool = small_pool().with_columns(
        pl.when(pl.col("player_id") == "pl-00")
        .then(None)
        .otherwise(pl.col("projected_points"))
        .alias("projected_points")
    )
    with pytest.raises(ValueError, match="null values"):
        optimize(pool, Settings())


def test_a_lineup_outside_the_squad_is_rejected() -> None:
    """The result type has to refuse a lineup its own squad cannot field."""
    with pytest.raises(ValueError, match="not in the squad"):
        Squad(
            players=("pl-1",), lineup=("pl-2",), lineup_points=0.0, bench_points=0.0, cost=500_000
        )


def test_real_pool_squads_satisfy_every_constraint(real_pool: pl.DataFrame) -> None:
    """Every squad the solver returns must be legal on all four constraint families."""
    settings = Settings(data_dir=REPO_ROOT / "data")
    for squad in optimize_top_k(real_pool, settings, 3):
        frame = squad_frame(real_pool, squad)
        assert frame.height == settings.squad_size
        lineup = frame.filter(pl.col("in_lineup"))
        assert lineup.height == settings.lineup_size

        squad_counts = dict(frame.group_by("position").len().iter_rows())
        lineup_counts = dict(lineup.group_by("position").len().iter_rows())
        for position in Position:
            assert squad_counts[position.value] == settings.squad_quota[position]
            assert lineup_counts[position.value] == settings.lineup_quota[position]

        assert squad.cost == frame.get_column("market_value").sum()
        assert squad.cost <= settings.budget
        assert frame.group_by("club").len().get_column("len").to_numpy().max() <= settings.club_cap


def test_real_pool_bench_costs_the_theoretical_minimum(real_pool: pl.DataFrame) -> None:
    """At ``bench_weight = 0`` the four fillers must cost the 2.0M floor, not merely approach it.

    The floor is the cheapest player at each bench position, which the club cap could in principle
    make unreachable. A failure means either the lexicographic second stage is missing or the cap
    is forcing a dearer filler — both worth surfacing rather than absorbing.
    """
    settings = Settings(data_dir=REPO_ROOT / "data")
    floor = sum(
        int(
            real_pool.filter(pl.col("position") == position.value)
            .get_column("market_value")
            .to_numpy()
            .min()
        )
        * count
        for position, count in settings.bench_quota.items()
    )
    assert floor == 2_000_000

    squad = optimize(real_pool, settings)
    bench = squad_frame(real_pool, squad).filter(~pl.col("in_lineup"))
    assert bench.get_column("market_value").sum() == floor

    # Minimising the bench is only worth doing because what it saves reaches the eleven: the whole
    # budget is spent, and all of it but the 2.0M floor funds the XI.
    assert squad.cost == settings.budget
    assert squad.cost - floor == 28_000_000


def test_real_pool_never_fields_a_deputy_goalkeeper(real_pool: pl.DataFrame) -> None:
    """The plan asks whether buying a club's number two needs forbidding. It does not.

    The step model prices a deputy at a tenth of a number one, so no price makes him worth
    fielding and the solver reaches that unaided. The *reserve* keeper is the opposite case: he is
    bought at the 500k floor precisely because he is somebody's deputy and will score nothing.
    """
    settings = Settings(data_dir=REPO_ROOT / "data")
    keepers = squad_frame(real_pool, optimize(real_pool, settings)).filter(
        pl.col("position") == Position.GOALKEEPER.value
    )
    assert keepers.filter(pl.col("in_lineup")).get_column("keeper_rank").to_list() == [1]
    assert keepers.filter(~pl.col("in_lineup")).get_column("keeper_rank").to_numpy().min() > 1


def test_relaxed_constraints_reproduce_the_naive_top_scorer_xi(real_pool: pl.DataFrame) -> None:
    """With the budget and the club cap lifted, the answer must be the obvious one.

    Scored on last season's points rather than the projection, so the target is the 54.9M naive XI
    quoted throughout the docs — the number that makes the budget the binding difficulty. This
    checks the objective and the positional quotas are wired to each other correctly.
    """
    naive = real_pool.with_columns(pl.col("points").cast(pl.Float64).alias("projected_points"))
    settings = Settings(data_dir=REPO_ROOT / "data", budget=10**9, club_cap=15)

    lineup = squad_frame(naive, optimize(naive, settings)).filter(pl.col("in_lineup"))
    top_scorers = pl.concat(
        naive.filter(pl.col("position") == position.value).sort("points", descending=True).head(n)
        for position, n in settings.lineup_quota.items()
    )
    assert set(lineup.get_column("player_id")) == set(top_scorers.get_column("player_id"))
    assert lineup.get_column("market_value").sum() == 54_900_000
