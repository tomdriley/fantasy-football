"""Season value: what a 15-item roster is actually worth.

The naive objective -- sum the best 10 items that fit the starting slots -- is
wrong, and wrong in a way that corrupts a third of the draft. It assigns exactly
zero to the 5 bench slots, so every bench candidate ties at zero marginal value
and an arbitrary tiebreak decides those picks. Breaking ties by raw payoff makes
the policy hoard quarterbacks; breaking them by value over replacement makes it
hoard defenses. Neither is a bug in the tiebreak: both are symptoms of an
objective with no bench term at all.

The lineup is chosen *weekly*, not once. Every player misses their bye week, and
most miss additional time. When a starter is unavailable, that week's slot is
filled by the next roster item that can legally occupy it -- or, failing that, by
a free agent. So a bench item is worth:

    expected starts  x  (its per-game payoff - the free agent's per-game payoff)

Both terms matter, and together they explain the observed replaceability table:
a backup defense almost never starts AND the free-agent defense is nearly as
good, so it is worthless; a spare running back starts often AND free-agent
running backs are far worse, so depth there is genuinely valuable.

**Wildcard slots are shared, but type minimums still bind.** Getting this right
took three attempts, each caught by a behavioural test:

1. Splitting the wildcard slots evenly across eligible types (0.67 each)
   truncated to zero whole slots, so any running back or receiver beyond the
   truncated count was priced at exactly zero -- reintroducing the very bug this
   module exists to fix.
2. Pooling all flex-eligible types as fully interchangeable removed the type
   minimums, producing rosters with *zero* running backs. A lineup needs 2 RB
   and 2 WR regardless of which items score most.
3. Correct: dedicated slots are filled by type, wildcard slots are then filled
   from whatever remains, and *bench* depth is measured against the slots that
   type could actually occupy -- its own dedicated slots plus the wildcards.
"""

from __future__ import annotations

from typing import Sequence

from . import config, pool

# Fantasy weeks in a season. Teams play 17 games across an 18-week calendar, so
# every player is unavailable for at least their bye.
WEEKS = 18
BYE_WEEKS = 1

# Games missed per season beyond the bye, derived from 2025 realized data
# (players with 8+ games, to exclude fringe roster churn). Notably running backs
# miss *fewer* games than quarterbacks here, the opposite of the common
# assumption, and team defenses miss none because they have no injury mechanism
# as a unit.
GAMES_MISSED = {
    "QB": 3.4,
    "RB": 2.4,
    "WR": 3.0,
    "TE": 2.6,
    "K": 1.8,
    "DEF": 0.0,
}

# Consensus pick index beyond which items are effectively free all season.
WAIVER_THRESHOLD = 150


def unavailability(pos: str) -> float:
    """Per-week probability an item of this type cannot be started."""
    missed = GAMES_MISSED.get(pos, 3.0) + BYE_WEEKS
    return min(max(missed / WEEKS, 0.0), 0.95)


def waiver_baselines(
    items: Sequence[pool.Item],
    cfg: config.LeagueConfig | None = None,
    threshold: int = WAIVER_THRESHOLD,
) -> dict[str, float]:
    """Payoff realistically obtainable at each type without spending a pick.

    Not the single best undrafted item: all agents compete for free agents, and
    only one can have the best one. Taking the best undrafted item as the
    baseline makes every unfilled starting slot look nearly free, which in
    testing produced rosters carrying eight receivers and no tight end -- the
    model believed a waiver tight end was almost as good as a drafted one, for
    every agent simultaneously.

    Contention is approximated by taking the item a mid-pack agent could expect
    to win, i.e. roughly half the agents deep into the free pool.
    """
    contention = max((cfg.num_agents // 2) if cfg else 5, 1)
    grouped: dict[str, list[float]] = {}
    for item in items:
        if item.adp is None or item.adp <= threshold:
            continue
        grouped.setdefault(item.pos, []).append(item.payoff)
    out: dict[str, float] = {}
    for position, payoffs in grouped.items():
        payoffs.sort(reverse=True)
        idx = min(contention - 1, len(payoffs) - 1)
        out[position] = payoffs[idx]
    return out


def _at_least(k: int, n: int, p: float) -> float:
    """P(at least k successes in n independent Bernoulli(p) trials)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    total = 0.0
    for i in range(k, n + 1):
        c = 1.0
        for j in range(i):
            c = c * (n - j) / (j + 1)
        total += c * (p ** i) * ((1 - p) ** (n - i))
    return total


def accessible_slots(pos: str, cfg: config.LeagueConfig) -> int:
    """Starting slots a bench item of this type could ever occupy.

    A spare running back can cover an injured starting running back *or* slide
    into a wildcard slot, so its depth is measured against both.
    """
    base = cfg.dedicated_slots.get(pos, 0)
    if pos in cfg.flex_types:
        base += cfg.flex_slots
    return base


def assign_starters(
    roster: Sequence[pool.Item], cfg: config.LeagueConfig
) -> tuple[list[pool.Item], dict[str, int]]:
    """Fill dedicated slots by type, then wildcard slots from what remains.

    Type minimums are binding: a lineup needs 2 RB and 2 WR regardless of which
    items score most. Only the wildcard slots are shared. Treating every
    flex-eligible type as interchangeable produces rosters that cannot field a
    legal lineup.
    """
    groups: dict[str, list[pool.Item]] = {}
    for item in roster:
        groups.setdefault(item.pos, []).append(item)
    for group in groups.values():
        group.sort(key=lambda i: -i.payoff)

    starters: list[pool.Item] = []
    used: set[int] = set()
    counts: dict[str, int] = {}
    for position, slots in cfg.dedicated_slots.items():
        for item in groups.get(position, [])[:slots]:
            starters.append(item)
            used.add(id(item))
            counts[position] = counts.get(position, 0) + 1

    if cfg.flex_slots:
        spare = sorted(
            (i for i in roster if id(i) not in used and i.pos in cfg.flex_types),
            key=lambda i: -i.payoff,
        )
        for item in spare[: cfg.flex_slots]:
            starters.append(item)
            used.add(id(item))
            counts[item.pos] = counts.get(item.pos, 0) + 1
    return starters, counts


def season_value(
    roster: Sequence[pool.Item],
    cfg: config.LeagueConfig,
    waivers: dict[str, float],
) -> float:
    """Expected season payoff of a roster, pricing bench slots properly."""
    starters, started = assign_starters(roster, cfg)
    starter_ids = {id(i) for i in starters}

    total = 0.0
    for item in starters:
        total += WEEKS * (1.0 - unavailability(item.pos)) * (item.payoff / WEEKS)

    # Unfilled starting slots must be covered by a free agent all season.
    for position, slots in cfg.dedicated_slots.items():
        missing = slots - started.get(position, 0)
        if missing > 0:
            total += missing * waivers.get(position, 0.0) * (
                1.0 - unavailability(position)
            )

    # Bench items are worth their surplus over a free agent, times how often
    # they are actually needed.
    by_pos: dict[str, list[pool.Item]] = {}
    for item in roster:
        if id(item) not in starter_ids:
            by_pos.setdefault(item.pos, []).append(item)
    for position, group in by_pos.items():
        group.sort(key=lambda i: -i.payoff)
        slots = accessible_slots(position, cfg)
        q = unavailability(position)
        waiver_pg = waivers.get(position, 0.0) / WEEKS
        for depth, item in enumerate(group, start=1):
            starts = WEEKS * _at_least(depth, max(slots, 1), q)
            total += starts * max(0.0, item.payoff / WEEKS - waiver_pg)
    return total


def marginal_season_value(
    roster: Sequence[pool.Item],
    item: pool.Item,
    cfg: config.LeagueConfig,
    waivers: dict[str, float],
) -> float:
    return season_value(list(roster) + [item], cfg, waivers) - season_value(
        roster, cfg, waivers
    )
