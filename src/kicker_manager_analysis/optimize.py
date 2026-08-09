"""Squad selection as an integer programme, solved with PuLP and CBC.

Two binaries per player: ``x_p`` for being one of the 15 bought, ``s_p`` for being one of the 11
who score, linked by ``s_p <= x_p``. The rest is the rule book — the squad and lineup quotas per
position, the budget, and at most three players from any one club.

Two things about the formulation are not incidental.

- **Bench and XI are solved jointly.** A 500k filler still consumes one of his club's three slots,
  so the bench cannot be chosen after the XI and bolted on; the club cap couples them.
- **At ``bench_weight = 0`` the bench is cost-degenerate.** The solver is pushed toward cheap
  fillers only insofar as the budget binds, and once the optimal XI leaves any slack *every*
  affordable bench is equally optimal — CBC may return any of them. :func:`optimize` therefore
  solves lexicographically: maximise points, then minimise cost among the squads that reach that
  optimum. That yields the cheapest point-optimal squad exactly, with no epsilon weight to tune.

Alternatives come from no-good cuts. Excluding a squad already found with
``sum over its members of x_p <= 14`` leaves everything else reachable, so the next solve returns
the best squad differing in at least one player. This is what makes the Phase 6 robustness pass
cheap, and it matters here for a second reason: with the outfield residual weights measured at
essentially zero, many squads tie on points, and seeing the ties is more honest than reporting
whichever one CBC happened to reach first.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Self

import polars as pl
import pulp
from pydantic import BaseModel, ConfigDict, model_validator

from kicker_manager_analysis.config import Settings
from kicker_manager_analysis.projection import EUROS_PER_MILLION

REQUIRED_COLUMNS: Final = ("player_id", "position", "club", "market_value", "projected_points")
"""Columns the programme reads, as produced by :func:`~.projection.project`."""

POINT_OPTIMALITY_TOLERANCE: Final = 1e-6
"""Relative slack allowed on the first stage's optimum while the second stage minimises cost.

CBC reports objective values in floating point, so demanding that the second stage match the
first exactly would occasionally cut off the very squad the first stage just found. Relative to a
season total of around a thousand points this is a thousandth of a point — orders of magnitude
below any difference the projection can resolve between two players.
"""

# Money enters the programme in millions, never in euros. Both are exact statements of the same
# constraints, but a cost objective of ~3e7 against points coefficients of ~1e2 leaves CBC unable
# to close the gap on the second stage at all: the real pool ran for over four minutes without
# terminating in euros and solves in a tenth of a second in millions. It is the same conditioning
# problem the curve fit hit, and the same fix.


class Squad(BaseModel):
    """One legal squad and the lineup within it.

    Players are held as ids rather than rows so that a squad stays comparable across pools and
    cheap to carry through a Monte-Carlo pass; :func:`squad_frame` joins them back to the data.

    Attributes:
        players: The ids of all bought players, in pool order.
        lineup: The subset of them that scores, in pool order.
        lineup_points: Projected points of the lineup, which is the number to compare squads on.
        bench_points: Projected points of the bench, which the objective counts only at
            ``bench_weight`` — zero by default, since a bench player scores only when his
            positional counterpart does not play at all.
        cost: Total market value of all bought players, in euros.
    """

    model_config = ConfigDict(frozen=True)

    players: tuple[str, ...]
    lineup: tuple[str, ...]
    lineup_points: float
    bench_points: float
    cost: int

    @model_validator(mode="after")
    def _check_membership(self) -> Self:
        """Reject a squad whose lineup is not drawn from its own players.

        Returns:
            The validated squad.

        Raises:
            ValueError: If a player appears twice, or a name in the lineup was never bought.
        """
        if len(set(self.players)) != len(self.players):
            raise ValueError("a player cannot be bought twice")
        outside = sorted(set(self.lineup) - set(self.players))
        if outside:
            raise ValueError(f"lineup contains players who are not in the squad: {outside}")
        return self

    @property
    def bench(self) -> tuple[str, ...]:
        """The bought players who do not score."""
        lineup = set(self.lineup)
        return tuple(player for player in self.players if player not in lineup)

    def objective(self, settings: Settings) -> float:
        """Return the value the optimizer maximised for this squad.

        Args:
            settings: Supplies the bench weight the squad was solved under.

        Returns:
            Lineup points plus the weighted bench points.
        """
        return self.lineup_points + settings.bench_weight * self.bench_points


@dataclass(frozen=True)
class _Programme:
    """A built but unsolved integer programme, plus the pieces both solve stages need.

    Attributes:
        problem: The PuLP problem, carrying every constraint but no objective yet.
        squad: One binary per pool player, set when he is bought.
        lineup: One binary per pool player, set when he scores.
        points: The objective to maximise first.
        cost: The objective to minimise second, among the squads that reach the first optimum,
            in millions of euros.
    """

    problem: pulp.LpProblem
    squad: list[pulp.LpVariable]
    lineup: list[pulp.LpVariable]
    points: pulp.LpAffineExpression
    cost: pulp.LpAffineExpression


def _validated_pool(projected: pl.DataFrame) -> pl.DataFrame:
    """Reduce a projected pool to the columns the programme reads, checking they are usable.

    Args:
        projected: Pool with expected points attached, from :func:`~.projection.project`.

    Returns:
        The pool with only :data:`REQUIRED_COLUMNS`, in that order.

    Raises:
        ValueError: If a required column is missing or carries nulls. A null would otherwise
            reach PuLP as a coefficient and fail deep inside the solver call.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in projected.columns]
    if missing:
        raise ValueError(f"the projected pool is missing columns: {missing}")

    pool = projected.select(REQUIRED_COLUMNS)
    null_columns = [
        column for column, count in pool.null_count().row(0, named=True).items() if count
    ]
    if null_columns:
        raise ValueError(f"the projected pool has null values in: {null_columns}")
    return pool


def _build(
    pool: pl.DataFrame, settings: Settings, cuts: Sequence[frozenset[str]] = ()
) -> _Programme:
    """Build the integer programme for one pool.

    Args:
        pool: Validated pool, as returned by :func:`_validated_pool`.
        settings: Supplies the quotas, the budget, the club cap and the bench weight.
        cuts: Squads already found. Each is excluded by a no-good cut, which forbids buying all
            of its members at once while leaving every other squad reachable.

    Returns:
        The programme, with no objective set — the caller decides which stage it is solving.
    """
    ids = pool.get_column("player_id").to_list()
    positions = pool.get_column("position").to_list()
    clubs = pool.get_column("club").to_list()
    millions = [value / EUROS_PER_MILLION for value in pool.get_column("market_value").to_list()]
    points = pool.get_column("projected_points").to_list()
    index = range(pool.height)

    problem = pulp.LpProblem("kicker_squad", pulp.LpMaximize)
    # Variables are created through the problem rather than constructed directly: PuLP 4.0 makes
    # the model own them, and the direct constructor is already deprecated.
    squad = [problem.add_variable(f"x_{i}", cat=pulp.LpBinary) for i in index]
    lineup = [problem.add_variable(f"s_{i}", cat=pulp.LpBinary) for i in index]

    for i in index:
        problem += lineup[i] <= squad[i], f"lineup_within_squad_{i}"

    for position, required in settings.squad_quota.items():
        members = [i for i in index if positions[i] == position.value]
        problem += pulp.lpSum(squad[i] for i in members) == required, f"squad_{position.value}"
        problem += (
            pulp.lpSum(lineup[i] for i in members) == settings.lineup_quota[position],
            f"lineup_{position.value}",
        )

    budget = settings.budget / EUROS_PER_MILLION
    problem += pulp.lpSum(millions[i] * squad[i] for i in index) <= budget, "budget"

    # Clubs are numbered rather than named: names carry spaces and dots, which PuLP would have to
    # mangle into constraint identifiers, and two clubs could then collide.
    for number, club in enumerate(dict.fromkeys(clubs)):
        members = [i for i in index if clubs[i] == club]
        problem += pulp.lpSum(squad[i] for i in members) <= settings.club_cap, f"club_{number}"

    for number, cut in enumerate(cuts):
        members = [i for i in index if ids[i] in cut]
        problem += (
            pulp.lpSum(squad[i] for i in members) <= settings.squad_size - 1,
            f"no_good_{number}",
        )

    weight = settings.bench_weight
    return _Programme(
        problem=problem,
        squad=squad,
        lineup=lineup,
        points=pulp.lpSum(
            points[i] * ((1.0 - weight) * lineup[i] + weight * squad[i]) for i in index
        ),
        cost=pulp.lpSum(millions[i] * squad[i] for i in index),
    )


def _solve(problem: pulp.LpProblem) -> bool:
    """Run CBC on a problem and report whether it proved an optimum.

    Args:
        problem: A problem with its objective already set.

    Returns:
        True if CBC found the optimum, False if the problem is infeasible — which is an ordinary
        outcome once enough no-good cuts have exhausted the pool.

    Raises:
        RuntimeError: On any other status. Unbounded or undefined means the model is wrong rather
            than the pool too tight, and silently returning nothing would hide that.
    """
    # PuLP 4.0 will drop PULP_CBC_CMD in favour of COIN_CMD against a separately installed CBC.
    # The bundled binary is what lets `uv sync` alone reproduce a run, so the migration waits for
    # the release that forces it.
    status = pulp.LpStatus[problem.solve(pulp.PULP_CBC_CMD(msg=False))]
    if status == "Optimal":
        return True
    if status == "Infeasible":
        return False
    raise RuntimeError(f"CBC returned status {status!r}, which means the model is malformed")


def _chosen(variables: list[pulp.LpVariable]) -> list[int]:
    """Return the pool indices whose binary the solver set.

    Args:
        variables: One binary per pool player, already solved.

    Returns:
        The indices set to one. Values are rounded rather than compared exactly, since CBC
        reports them in floating point.
    """
    return [index for index, variable in enumerate(variables) if float(variable.varValue) > 0.5]


def _solve_lexicographic(
    pool: pl.DataFrame, settings: Settings, cuts: Sequence[frozenset[str]]
) -> Squad | None:
    """Maximise points, then minimise cost among the squads that reach that optimum.

    The second stage is what makes the bench cheapest rather than arbitrary. Without it, any bench
    the budget can afford is equally optimal at ``bench_weight = 0`` and CBC returns whichever it
    reaches first.

    Args:
        pool: Validated pool, as returned by :func:`_validated_pool`.
        settings: Rules to solve under.
        cuts: Squads already found, excluded by no-good cuts.

    Returns:
        The cheapest point-optimal squad, or None if no legal squad remains.

    Raises:
        RuntimeError: If the second stage cannot reproduce the first stage's optimum, which would
            mean the point floor or the objective is misstated rather than the pool infeasible.
    """
    programme = _build(pool, settings, cuts)
    programme.problem.setObjective(programme.points)
    if not _solve(programme.problem):
        return None

    best = float(pulp.value(programme.problem.objective))
    floor = best - POINT_OPTIMALITY_TOLERANCE * max(abs(best), 1.0)
    programme.problem.addConstraint(programme.points >= floor, "point_optimality")
    programme.problem.sense = pulp.LpMinimize
    programme.problem.setObjective(programme.cost)
    if not _solve(programme.problem):
        raise RuntimeError(
            f"no squad reaches the point optimum {best} that was just proved attainable"
        )

    bought = _chosen(programme.squad)
    starters = set(_chosen(programme.lineup))
    ids = pool.get_column("player_id").to_list()
    points = pool.get_column("projected_points").to_list()
    values = pool.get_column("market_value").to_list()
    return Squad(
        players=tuple(ids[i] for i in bought),
        lineup=tuple(ids[i] for i in bought if i in starters),
        lineup_points=sum(points[i] for i in bought if i in starters),
        bench_points=sum(points[i] for i in bought if i not in starters),
        cost=sum(values[i] for i in bought),
    )


def optimize_top_k(projected: pl.DataFrame, settings: Settings, k: int) -> tuple[Squad, ...]:
    """Return the best ``k`` squads, each differing from the others in at least one player.

    Squads come out in descending order of the objective. Ties are expected rather than
    exceptional: with the measured outfield blend weights at essentially zero, two ways of
    spending the same money on the same positions project identically, and the enumeration is how
    that degeneracy becomes visible.

    Args:
        projected: Pool with expected points attached, from :func:`~.projection.project`.
        settings: Rules to solve under.
        k: How many squads to enumerate.

    Returns:
        Up to ``k`` squads. Fewer are returned when the pool runs out of distinct legal squads,
        which the no-good cuts eventually force on a small pool.

    Raises:
        ValueError: If ``k`` is not positive, the pool is missing a required column, or no legal
            squad exists at all — the last usually meaning the club cap and the budget cannot be
            satisfied together, which :func:`~.data.validate_pool` cannot detect.
    """
    if k < 1:
        raise ValueError(f"k must be positive, got {k}")

    pool = _validated_pool(projected)
    squads: list[Squad] = []
    cuts: list[frozenset[str]] = []
    for _ in range(k):
        squad = _solve_lexicographic(pool, settings, cuts)
        if squad is None:
            break
        squads.append(squad)
        cuts.append(frozenset(squad.players))

    if not squads:
        raise ValueError(
            f"no squad of {settings.squad_size} satisfies the quotas, a budget of "
            f"{settings.budget} and at most {settings.club_cap} players per club"
        )
    return tuple(squads)


def optimize(projected: pl.DataFrame, settings: Settings) -> Squad:
    """Return the cheapest squad among those maximising projected points.

    Args:
        projected: Pool with expected points attached, from :func:`~.projection.project`.
        settings: Rules to solve under.

    Returns:
        The optimal squad.

    Raises:
        ValueError: If the pool is unusable or admits no legal squad.
    """
    return optimize_top_k(projected, settings, 1)[0]


def squad_frame(projected: pl.DataFrame, squad: Squad) -> pl.DataFrame:
    """Join a squad back to the pool it was picked from.

    Args:
        projected: The pool the squad was solved against.
        squad: A solved squad.

    Returns:
        The squad's rows with an ``in_lineup`` flag, ordered by position and then by the players
        who score first.
    """
    lineup = set(squad.lineup)
    return (
        projected.filter(pl.col("player_id").is_in(set(squad.players)))
        .with_columns(pl.col("player_id").is_in(lineup).alias("in_lineup"))
        .sort(
            ["position", "in_lineup", "projected_points"],
            descending=[False, True, True],
        )
    )
