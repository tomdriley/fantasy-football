"""Non-circular backtest against realized outcomes.

The failure mode this module exists to avoid: if a strategy claims items using
forecast vector F and is then scored using that same F, it wins by construction
and the result is meaningless. Here, **claims are made using pre-season
information only** (the prior season's projections and consensus order) and
**scores are computed from realized outcomes** that were unknowable at draft
time. Forecast error is therefore fully in play -- in 2025 one top-3 consensus
pick returned 77% of its projection while another returned 120%.

Three strategies compete:

* ``autopick`` -- the platform default. Strict consensus order, subject only to
  not exceeding roster slots. This is the baseline the project set out to beat.
* ``heuristic`` -- five lines of domain common sense: take the highest-consensus
  item that fills an unfilled starting slot; kicker last, defense second to last.
  **This is the honest baseline.** If the rollout optimizer cannot beat it, the
  optimizer's complexity is unjustified.
* ``optimizer`` -- the rollout engine.

Scoring uses the best legal starting lineup from realized season totals. This
slightly penalises depth strategies, since bench items provide injury insurance
that season totals cannot capture. The bias applies to every strategy equally,
and it works *against* the optimizer, so a win here is a conservative result.
"""

from __future__ import annotations

import dataclasses
import random
from typing import Callable, Sequence

from . import availability, config, optimizer, pool, scoring, season, valuation

Strategy = Callable[..., int]


@dataclasses.dataclass(slots=True)
class DraftContext:
    cfg: config.LeagueConfig
    board: list[pool.Item]
    vor: dict[str, float]
    waivers: dict[str, float]
    rng: random.Random
    bot_seats: int = 0


def _slot_capacity(cfg: config.LeagueConfig) -> dict[str, int]:
    """Maximum useful count per type: dedicated slots plus wildcard capacity."""
    caps = dict(cfg.dedicated_slots)
    for position in cfg.flex_types:
        caps[position] = caps.get(position, 0) + cfg.flex_slots
    return caps


def pick_autopick(
    roster: Sequence[pool.Item], alive: list[int], ctx: DraftContext, **_
) -> int:
    """Platform default: highest consensus item that does not overflow a slot."""
    caps = _slot_capacity(ctx.cfg)
    counts: dict[str, int] = {}
    for item in roster:
        counts[item.pos] = counts.get(item.pos, 0) + 1
    for idx in alive:
        position = ctx.board[idx].pos
        if counts.get(position, 0) < caps.get(position, 0):
            return idx
    return alive[0]


def pick_heuristic(
    roster: Sequence[pool.Item], alive: list[int], ctx: DraftContext,
    picks_remaining: int = 0, **_
) -> int:
    """Highest consensus item filling an unfilled starting slot; K and DEF last."""
    cfg = ctx.cfg
    counts: dict[str, int] = {}
    for item in roster:
        counts[item.pos] = counts.get(item.pos, 0) + 1

    deferred = {"K", "DEF"}
    missing = {
        position
        for position, slots in cfg.dedicated_slots.items()
        if counts.get(position, 0) < slots
    }
    # Force the deferred types in only when picks would otherwise run out.
    mandatory = missing & deferred
    if mandatory and picks_remaining <= len(missing):
        for idx in alive:
            if ctx.board[idx].pos in mandatory:
                return idx

    wanted = missing - deferred
    if wanted:
        for idx in alive:
            if ctx.board[idx].pos in wanted:
                return idx

    caps = _slot_capacity(cfg)
    for idx in alive:
        position = ctx.board[idx].pos
        if position in deferred:
            continue
        # Depth is only useful where a type can actually reach a starting slot.
        allowance = caps.get(position, 0) + (2 if position in cfg.flex_types else 0)
        if counts.get(position, 0) < allowance:
            return idx
    return alive[0]


def pick_projection_greedy(
    roster: Sequence[pool.Item], alive: list[int], ctx: DraftContext,
    picks_remaining: int = 0, **_
) -> int:
    """Highest PROJECTED payoff subject to slot caps -- autopick's logic, better input.

    Isolates the value of the search from the value of the data. Autopick orders
    by consensus; this orders by projected payoff, which measurably predicts
    realized outcomes better. If this beats the rollout optimizer, the objective
    or the search is the problem, not the data.
    """
    cfg = ctx.cfg
    counts: dict[str, int] = {}
    for item in roster:
        counts[item.pos] = counts.get(item.pos, 0) + 1
    missing = {
        position
        for position, slots in cfg.dedicated_slots.items()
        if counts.get(position, 0) < slots
    }
    if missing and picks_remaining <= len(missing):
        best = [i for i in alive if ctx.board[i].pos in missing]
        if best:
            return max(best, key=lambda i: ctx.board[i].payoff)

    caps = _slot_capacity(cfg)
    eligible = [
        i for i in alive
        if counts.get(ctx.board[i].pos, 0) < caps.get(ctx.board[i].pos, 0)
    ]
    if not eligible:
        eligible = alive
    return max(eligible, key=lambda i: ctx.board[i].payoff)


def pick_optimizer(
    roster: Sequence[pool.Item], alive: list[int], ctx: DraftContext,
    seat: int = 1, current_pick: int = 1, trials: int = 6, **_
) -> int:
    sub = [ctx.board[i] for i in alive]
    recs = optimizer.recommend(
        list(roster), sub, ctx.cfg, seat=seat, current_pick=current_pick,
        vor=ctx.vor, waivers=ctx.waivers, num_candidates=4, trials=trials,
        reach=availability.DEFAULT_REACH, bot_seats=ctx.bot_seats, rng=ctx.rng,
    )
    chosen = recs[0].item
    return alive[sub.index(chosen)]


def pick_hybrid(
    roster: Sequence[pool.Item], alive: list[int], ctx: DraftContext,
    picks_remaining: int = 0, window: int = 12, **_
) -> int:
    """Consensus order for robustness, projections to choose within a window.

    The backtest showed the platform default is strong: ordering by consensus
    with per-type caps implicitly encodes scarcity, because capping the
    single-slot types forces the remaining picks into the types that actually
    accumulate value. It is not myopic in the way this project assumed.

    Two measured facts motivate this blend. Projections predict realized
    outcomes better than consensus order (Spearman +0.55 vs +0.38), but ordering
    purely by projection walks into the trap of spending premium picks on the
    highest-scoring, least-scarce type. Restricting the choice to a short window
    of the consensus board keeps claims near market value while using the better
    signal to choose within it.

    Caps are also tightened relative to the platform default: no second
    quarterback and no third tight end, since surplus at a one-slot type is worth
    little against a deep free-agent pool.
    """
    cfg = ctx.cfg
    counts: dict[str, int] = {}
    for item in roster:
        counts[item.pos] = counts.get(item.pos, 0) + 1

    missing = {
        position
        for position, slots in cfg.dedicated_slots.items()
        if counts.get(position, 0) < slots
    }
    if missing and picks_remaining <= len(missing):
        forced = [i for i in alive if ctx.board[i].pos in missing]
        if forced:
            return forced[0]

    caps = _slot_capacity(cfg)
    caps["QB"] = 1
    caps["TE"] = min(caps.get("TE", 1), 2)
    eligible = [
        i for i in alive
        if counts.get(ctx.board[i].pos, 0) < caps.get(ctx.board[i].pos, 0)
    ]
    if not eligible:
        eligible = alive
    return max(eligible[:window], key=lambda i: ctx.board[i].payoff)


def pick_depth(
    roster: Sequence[pool.Item], alive: list[int], ctx: DraftContext,
    picks_remaining: int = 0, **_
) -> int:
    """Consensus order, one each of the single-slot types, everything else depth.

    The backtest isolated roster *shape* as the dominant factor, ahead of player
    selection: a strategy that drafted better players by realized total still
    scored lower each week because it carried fewer flex-eligible bodies. With
    seven of ten starting slots open to RB/WR/TE and starters missing time, spare
    flex-eligible players convert directly into filled lineups.

    So: exactly one quarterback, kicker and defense, and every remaining pick
    spent on flex-eligible depth in consensus order.
    """
    cfg = ctx.cfg
    counts: dict[str, int] = {}
    for item in roster:
        counts[item.pos] = counts.get(item.pos, 0) + 1

    missing = {
        position
        for position, slots in cfg.dedicated_slots.items()
        if counts.get(position, 0) < slots
    }
    if missing and picks_remaining <= len(missing):
        forced = [i for i in alive if ctx.board[i].pos in missing]
        if forced:
            return forced[0]

    single = {p: 1 for p in cfg.dedicated_slots if p not in cfg.flex_types}
    for idx in alive:
        position = ctx.board[idx].pos
        if position in single:
            if counts.get(position, 0) >= single[position]:
                continue
            # Defer the near-worthless single-slot types until forced.
            if position in ("K", "DEF"):
                continue
        return idx
    return alive[0]


STRATEGIES: dict[str, Strategy] = {
    "depth": pick_depth,
    "hybrid": pick_hybrid,
    "autopick": pick_autopick,
    "heuristic": pick_heuristic,
    "optimizer": pick_optimizer,
    "projection": pick_projection_greedy,
}


def run_draft(
    ctx: DraftContext, strategy_by_seat: dict[int, str]
) -> dict[int, list[pool.Item]]:
    """Run one full draft. Returns each seat's roster."""
    cfg = ctx.cfg
    total = cfg.rounds * cfg.num_agents
    seat_of_pick: dict[int, int] = {}
    remaining: dict[int, int] = {}
    for seat in range(1, cfg.num_agents + 1):
        for pick in cfg.pick_numbers(seat):
            seat_of_pick[pick] = seat
        remaining[seat] = cfg.rounds

    rosters: dict[int, list[pool.Item]] = {
        s: [] for s in range(1, cfg.num_agents + 1)
    }
    alive = list(range(len(ctx.board)))
    for pick_no in range(1, total + 1):
        if not alive:
            break
        seat = seat_of_pick[pick_no]
        name = strategy_by_seat.get(seat, "autopick")
        idx = STRATEGIES[name](
            rosters[seat], alive, ctx,
            seat=seat, current_pick=pick_no, picks_remaining=remaining[seat],
        )
        rosters[seat].append(ctx.board[idx])
        remaining[seat] -= 1
        alive.remove(idx)
    return rosters


def score_roster_weekly(
    roster: Sequence[pool.Item],
    realized: dict[str, float],
    games: dict[str, float],
    cfg: config.LeagueConfig,
    *,
    weeks: int = 17,
    seed: int = 0,
) -> float:
    """Season score with weekly lineups and realized availability.

    The starting-lineup-only metric gives zero credit for bench depth, which is
    precisely what an injury-aware objective is buying. Since realized games
    played are known per player, availability can be replayed: a player who
    appeared in 12 of 17 games is available in 12 randomly chosen weeks, and
    each week's lineup is filled from whoever is available.

    Availability draws are keyed on player id so the same player is available in
    the same weeks for every strategy being compared -- common random numbers,
    so differences reflect roster construction rather than sampling noise.
    """
    schedule: dict[str, set[int]] = {}
    for item in roster:
        played = int(round(games.get(item.player_id, 0.0)))
        played = max(0, min(weeks, played))
        rng = random.Random(f"{seed}:{item.player_id}")
        schedule[item.player_id] = set(rng.sample(range(weeks), played))

    per_game = {
        item.player_id: (
            realized.get(item.player_id, 0.0) / max(games.get(item.player_id, 0.0), 1.0)
        )
        for item in roster
    }

    total = 0.0
    for week in range(weeks):
        available = [
            pool.Item(
                player_id=i.player_id, name=i.name, pos=i.pos, team=i.team,
                payoff=per_game.get(i.player_id, 0.0), adp=i.adp, games=i.games,
            )
            for i in roster
            if week in schedule.get(i.player_id, ())
        ]
        starters, _ = season.assign_starters(available, cfg)
        total += sum(i.payoff for i in starters)
    return total


def realized_games(stats: list[dict]) -> dict[str, float]:
    """Games actually played, by player id."""
    out: dict[str, float] = {}
    for record in stats:
        pid = str(record.get("player_id") or "")
        if pid:
            out[pid] = float((record.get("stats") or {}).get("gp") or 0.0)
    return out


def score_roster(
    roster: Sequence[pool.Item],
    realized: dict[str, float],
    cfg: config.LeagueConfig,
) -> float:
    """Best legal starting lineup, valued with realized season totals."""
    scored = [
        pool.Item(
            player_id=i.player_id, name=i.name, pos=i.pos, team=i.team,
            payoff=realized.get(i.player_id, 0.0), adp=i.adp, games=i.games,
        )
        for i in roster
    ]
    starters, _ = season.assign_starters(scored, cfg)
    return sum(i.payoff for i in starters)


def build_context(
    cfg: config.LeagueConfig,
    projections: list[dict],
    *,
    seed: int = 0,
    board_size: int = 190,
) -> tuple[DraftContext, dict[str, float]]:
    """Board and valuations from PRE-SEASON information only."""
    items = pool.build(projections, cfg.scoring_weights)
    baselines = valuation.compute_baselines(items, cfg)
    vor = {
        (i.player_id or i.name): valuation.value_over_replacement(i, baselines)
        for i in items
    }
    waivers = season.waiver_baselines(items, cfg)
    board = availability.consensus_order([i for i in items if i.adp is not None])
    ctx = DraftContext(
        cfg=cfg, board=board[:board_size], vor=vor, waivers=waivers,
        rng=random.Random(seed),
    )
    return ctx, waivers


def realized_payoffs(
    stats: list[dict], weights: dict[str, float]
) -> dict[str, float]:
    """Actual season payoff by player id -- unknowable at draft time."""
    out: dict[str, float] = {}
    for record in stats:
        pid = str(record.get("player_id") or "")
        if not pid:
            continue
        out[pid] = scoring.payoff(record.get("stats") or {}, weights)
    return out
