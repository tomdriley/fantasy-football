"""The claim decision.

Objective: maximize the expected value of the **final starting lineup**, not the
sum of claimed items. Surplus items score zero, so a correct objective must
evaluate a roster through the lineup constraints.

Method: one-step lookahead with rollout evaluation. For each candidate, claim it,
then simulate the remainder of the draft -- opponents via the opponent model, our
own future picks via a greedy continuation policy -- and average the resulting
final lineup value over many trials.

This formulation supplies the dimension that value-over-replacement lacks: *when*
an item can be obtained. Scarcity, roster needs and claim timing all fall out of
the objective rather than being imposed as rules. In particular, the ordering of
low-value mandatory slots (kicker, defense) emerges from opportunity cost: a type
whose value barely decays is always better deferred, because claiming it now
forfeits a scarcer item that will not survive.
"""

from __future__ import annotations

import dataclasses
import random
from typing import Sequence

from . import availability, config, pool, season


@dataclasses.dataclass(slots=True)
class Recommendation:
    item: pool.Item
    expected_lineup_value: float
    immediate_value: float
    survival: float | None = None
    notes: tuple[str, ...] = ()


def lineup_value(roster: Sequence[pool.Item], cfg: config.LeagueConfig) -> float:
    """Best achievable starting-lineup payoff from a roster.

    Dedicated slots take the best items of their type; wildcard slots then take
    the best remaining flex-eligible items. Everything else contributes zero.
    """
    groups: dict[str, list[pool.Item]] = {}
    for item in roster:
        groups.setdefault(item.pos, []).append(item)
    for group in groups.values():
        group.sort(key=lambda i: -i.payoff)

    total = 0.0
    used: set[int] = set()
    for position, count in cfg.dedicated_slots.items():
        for item in groups.get(position, [])[:count]:
            total += item.payoff
            used.add(id(item))

    if cfg.flex_slots:
        spare = [
            i for i in roster if id(i) not in used and i.pos in cfg.flex_types
        ]
        spare.sort(key=lambda i: -i.payoff)
        for item in spare[: cfg.flex_slots]:
            total += item.payoff
    return total


def unfilled_slots(
    roster: Sequence[pool.Item], cfg: config.LeagueConfig
) -> dict[str, int]:
    """Dedicated starting slots this roster still cannot fill."""
    have: dict[str, int] = {}
    for item in roster:
        have[item.pos] = have.get(item.pos, 0) + 1
    out: dict[str, int] = {}
    for position, slots in cfg.dedicated_slots.items():
        missing = slots - have.get(position, 0)
        if missing > 0:
            out[position] = missing
    return out


def mandatory_filter(
    roster: Sequence[pool.Item],
    cfg: config.LeagueConfig,
    picks_remaining: int,
) -> set[str] | None:
    """Types we must claim now to still field a legal lineup, or None.

    This is a *feasibility* constraint, not a strategy heuristic. A kicker is
    worth only a few points over a free agent, so it never wins a value
    comparison -- yet a roster that cannot field one is illegal. When remaining
    picks exactly equal remaining mandatory slots, choice ends and the roster
    must be completed.
    """
    missing = unfilled_slots(roster, cfg)
    needed = sum(missing.values())
    if needed and picks_remaining <= needed:
        return set(missing)
    return None


def marginal_value(
    roster: Sequence[pool.Item],
    item: pool.Item,
    cfg: config.LeagueConfig,
    waivers: dict[str, float],
) -> float:
    """Increase in expected SEASON value from adding `item`.

    Delegates to the season objective rather than a single static lineup. The
    single-lineup version prices all 5 bench slots at exactly zero, which leaves
    a third of the draft decided by an arbitrary tiebreak -- the cause of both
    the quarterback- and defense-hoarding pathologies.
    """
    return season.marginal_season_value(roster, item, cfg, waivers)


def payoff_order_by_position(board: Sequence[pool.Item]) -> dict[str, list[int]]:
    """Board indices grouped by type, each sorted by descending payoff.

    Built once per decision. The continuation policy needs the best remaining
    item of each type, and the board is ordered by consensus rather than payoff,
    so finding it by scanning cost a linear pass over every remaining player on
    every simulated pick. Walking a precomputed order instead stops at the first
    item still available.
    """
    out: dict[str, list[int]] = {}
    for idx, item in enumerate(board):
        out.setdefault(item.pos, []).append(idx)
    for indices in out.values():
        indices.sort(key=lambda i: -board[i].payoff)
    return out


def _greedy_choice(
    roster: list[pool.Item],
    alive: list[int],
    board: Sequence[pool.Item],
    cfg: config.LeagueConfig,
    vor: dict[str, float],
    waivers: dict[str, float],
    picks_remaining: int,
    by_pos: dict[str, list[int]] | None = None,
    alive_set: set[int] | None = None,
) -> int:
    """Continuation policy: take the largest immediate lineup gain.

    Ties are broken by **value over replacement, not raw payoff**. This matters
    enormously in late rounds, where every candidate adds zero to the starting
    lineup and the tiebreak decides the pick outright. Breaking ties by raw
    payoff reintroduces the central trap of this whole problem -- the type with
    the highest raw payoff (QB) is the least scarce -- and causes the policy to
    hoard backups that can never be started. Value over replacement is negative
    for such items, so it correctly prefers genuine bench depth instead.
    """
    required = mandatory_filter(roster, cfg, picks_remaining)

    # Within a single type, the highest-payoff item always dominates: both the
    # starter term and the bench term of the objective are monotonic in payoff,
    # and so is the value-over-replacement tiebreak. So only the best remaining
    # item of each type can win, which reduces the candidate scan from ~120 to
    # at most 6 and leaves the result identical.
    best_per_pos: dict[str, int] = {}
    if by_pos is not None and alive_set is not None:
        for position, ordered in by_pos.items():
            if required and position not in required:
                continue
            for idx in ordered:
                if idx in alive_set:
                    best_per_pos[position] = idx
                    break
    else:
        for idx in alive:
            item = board[idx]
            if required and item.pos not in required:
                continue
            incumbent = best_per_pos.get(item.pos)
            if incumbent is None or item.payoff > board[incumbent].payoff:
                best_per_pos[item.pos] = idx
    if not best_per_pos:
        return alive[0]

    base = season.season_value(roster, cfg, waivers)
    extended = list(roster)
    extended.append(None)
    best_idx = next(iter(best_per_pos.values()))
    best_key = (-1e18, -1e18)
    for idx in best_per_pos.values():
        item = board[idx]
        extended[-1] = item
        gain = season.season_value(extended, cfg, waivers) - base
        key = (gain, vor.get(item.player_id or item.name, 0.0))
        if key > best_key:
            best_key = key
            best_idx = idx
    return best_idx


def league_type_budget(cfg: config.LeagueConfig) -> dict[str, int]:
    """League-wide claims available per type, given every agent's slot needs.

    Opponents do not consume the board in pure consensus order: an agent holding
    a quarterback will not take a second, so only about one per agent is ever
    claimed. Modelling opponents as unconstrained consensus-followers therefore
    badly overstates scarcity at the single-slot types -- there are 22 viable
    quarterbacks on the board but only ~10 will ever be taken, so the tenth-best
    survives far longer than an unconstrained model predicts.
    """
    budget: dict[str, int] = {}
    for position, slots in cfg.dedicated_slots.items():
        per_agent = slots
        if position in cfg.flex_types:
            per_agent += cfg.flex_slots
        budget[position] = per_agent * cfg.num_agents
    return budget


def rollout(
    roster: list[pool.Item],
    alive: list[int],
    board: Sequence[pool.Item],
    cfg: config.LeagueConfig,
    my_remaining_picks: Sequence[int],
    current_pick: int,
    model: availability.OpponentModel,
    bot_seats: int | set[int],
    total_picks: int,
    vor: dict[str, float],
    waivers: dict[str, float],
    horizon: int = 0,
    by_pos: dict[str, list[int]] | None = None,
) -> float:
    """Simulate the rest of the draft and return our final lineup value.

    `horizon` limits how many of our own future picks are simulated. Distant
    picks contribute little to a comparison between candidates available now,
    because by then the board has diverged so far that the same continuation is
    reachable from any of them. Truncating trades a small amount of accuracy for
    a large amount of speed, which matters against a 60 second deadline.
    """
    roster = list(roster)
    alive = list(alive)
    alive_set = set(alive)
    upcoming = sorted(p for p in my_remaining_picks if p > current_pick)
    if horizon > 0:
        upcoming = upcoming[:horizon]
        total_picks = min(total_picks, upcoming[-1] if upcoming else current_pick)
    mine = set(upcoming)
    left = len(upcoming)
    budget = league_type_budget(cfg)
    for item in roster:
        budget[item.pos] = budget.get(item.pos, 0) - 1
    for pick_no in range(current_pick + 1, total_picks + 1):
        if not alive:
            break
        if pick_no in mine:
            idx = _greedy_choice(roster, alive, board, cfg, vor, waivers, left,
                                 by_pos, alive_set)
            roster.append(board[idx])
            left -= 1
        else:
            if isinstance(bot_seats, set):
                # Bot seats are known exactly, so their picks are not merely
                # likely to be deterministic -- they *are* deterministic, and at
                # which pick number is known in advance. Sampling their
                # incidence instead throws that away: for a middle seat every
                # pick between the first and second turn is a bot, while a
                # frequency model would treat fewer than half of them as such.
                is_bot = cfg.seat_of_pick(pick_no) in bot_seats
            else:
                is_bot = model.rng.random() < (bot_seats / max(cfg.num_agents - 1, 1))
            # Opponents never reach deeper than the model's capped offset, so
            # only the first few eligible items can ever be selected. Filtering
            # the whole remaining board here cost 63 million dict lookups per
            # recommendation; stopping early is exact, not an approximation.
            claimable = []
            for i in alive:
                if budget.get(board[i].pos, 0) > 0:
                    claimable.append(i)
                    if len(claimable) > availability.MAX_REACH:
                        break
            idx = model.claim(claimable or alive, deterministic=is_bot)
        budget[board[idx].pos] = budget.get(board[idx].pos, 0) - 1
        alive.remove(idx)
        alive_set.discard(idx)
    return season.season_value(roster, cfg, waivers)


def recommend(
    roster: Sequence[pool.Item],
    board: Sequence[pool.Item],
    cfg: config.LeagueConfig,
    *,
    seat: int,
    current_pick: int,
    vor: dict[str, float],
    waivers: dict[str, float],
    num_candidates: int = 8,
    trials: int = 60,
    reach: float = availability.DEFAULT_REACH,
    bot_seats: int | set[int] = 0,
    horizon: int = 0,
    rng: random.Random | None = None,
) -> list[Recommendation]:
    """Rank candidate claims by expected final lineup value.

    `board` must be in consensus order and contain only unclaimed items.
    """
    rng = rng or random.Random()
    model = availability.OpponentModel(reach=reach, rng=rng)
    total_picks = cfg.rounds * cfg.num_agents
    my_picks = cfg.pick_numbers(seat)
    roster = list(roster)

    # Candidate shortlist: best immediate lineup gain, plus the top of the board.
    picks_left = len([p for p in my_picks if p >= current_pick])
    required = mandatory_filter(roster, cfg, picks_left)
    candidate_idx = [
        i for i, it in enumerate(board[:80]) if not required or it.pos in required
    ]
    if not candidate_idx:
        candidate_idx = list(range(min(80, len(board))))
    scored = [
        (
            marginal_value(roster, board[idx], cfg, waivers),
            vor.get(board[idx].player_id or board[idx].name, 0.0),
            idx,
        )
        for idx in candidate_idx
    ]
    scored.sort(key=lambda t: (-t[0], -t[1]))
    shortlist = [idx for _, _, idx in scored[:num_candidates]]
    if not required:
        for idx in range(min(3, len(board))):
            if idx not in shortlist:
                shortlist.append(idx)

    by_pos = payoff_order_by_position(board)
    results: list[Recommendation] = []
    for idx in shortlist:
        item = board[idx]
        alive = [i for i in range(len(board)) if i != idx]
        total = 0.0
        for _ in range(trials):
            total += rollout(
                roster + [item],
                alive,
                board,
                cfg,
                my_picks,
                current_pick,
                model,
                bot_seats,
                total_picks,
                vor,
                waivers,
                horizon,
                by_pos,
            )
        results.append(
            Recommendation(
                item=item,
                expected_lineup_value=total / trials,
                immediate_value=marginal_value(roster, item, cfg, waivers),
            )
        )
    results.sort(key=lambda r: -r.expected_lineup_value)
    return results
