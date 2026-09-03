"""Live draft advisor.

Polls the claim feed and renders ranked recommendations. The tool advises; the
operator clicks. It never submits a claim.

The design target is not compute. A decision costs about 0.2 seconds of a 60
second budget, so roughly 99.7% of the deadline is human reading and clicking
time. Everything here optimises that: three options rather than two hundred, a
one line reason, and an explicit signal for when the choice does not matter so
attention is spent only where it changes the outcome.
"""

from __future__ import annotations

import dataclasses
from typing import Sequence

from . import availability, client, config, optimizer, pool, season, shrinkage, valuation

BAR = "=" * 74


@dataclasses.dataclass(slots=True)
class BoardState:
    claimed: set[str]
    my_roster: list[pool.Item]
    picks_made: int
    my_seat: int | None
    on_the_clock: int | None


def load_board(cfg: config.LeagueConfig, lam: float = 0.7) -> list[pool.Item]:
    """Consensus-ordered board with forecasts shrunk toward the market prior."""
    items = pool.build(client.projections(cfg.season), cfg.scoring_weights)
    if lam:
        items = shrinkage.shrink(items, lam)
    return availability.consensus_order([i for i in items if i.adp is not None])


def read_state(
    cfg: config.LeagueConfig, board: Sequence[pool.Item], my_user_id: str
) -> BoardState:
    """Current draft state from the live claim feed."""
    picks = client.draft_picks(cfg.draft_id)
    by_id = {i.player_id: i for i in board}
    claimed: set[str] = set()
    mine: list[pool.Item] = []
    seat = None
    for pick in picks:
        pid = str(pick.get("player_id") or "")
        claimed.add(pid)
        if str(pick.get("picked_by") or "") == my_user_id:
            seat = pick.get("draft_slot") or seat
            item = by_id.get(pid)
            if item is not None:
                mine.append(item)
    return BoardState(
        claimed=claimed,
        my_roster=mine,
        picks_made=len(picks),
        my_seat=seat,
        on_the_clock=(len(picks) % cfg.num_agents) + 1,
    )


def _unfilled(roster: Sequence[pool.Item], cfg: config.LeagueConfig) -> dict[str, int]:
    """Dedicated starting slots this roster cannot fill."""
    counts: dict[str, int] = {}
    for item in roster:
        counts[item.pos] = counts.get(item.pos, 0) + 1
    return {
        position: max(0, slots - counts.get(position, 0))
        for position, slots in cfg.dedicated_slots.items()
    }


def _slot_status(roster: Sequence[pool.Item], cfg: config.LeagueConfig) -> str:
    counts: dict[str, int] = {}
    for item in roster:
        counts[item.pos] = counts.get(item.pos, 0) + 1
    parts = []
    for position, slots in cfg.starting_slots.items():
        if position == "FLEX":
            continue
        have = counts.get(position, 0)
        parts.append(f"{position}{'#' * min(have, slots)}{'_' * max(0, slots - have)}")
    return " ".join(parts)


def recommend_now(
    cfg: config.LeagueConfig,
    board: Sequence[pool.Item],
    state: BoardState,
    seat: int,
    *,
    trials: int = 30,
    horizon: int = 8,
) -> tuple[list[optimizer.Recommendation], dict[str, float]]:
    available = [i for i in board if i.player_id not in state.claimed]
    baselines = valuation.compute_baselines(available, cfg)
    vor = {
        (i.player_id or i.name): valuation.value_over_replacement(i, baselines)
        for i in available
    }
    # Decide with the pessimistic waiver assumption; see season.objective_waivers.
    waivers = season.objective_waivers(available)
    current = state.picks_made + 1
    recs = optimizer.recommend(
        state.my_roster, available, cfg, seat=seat, current_pick=current,
        vor=vor, waivers=waivers, trials=trials, horizon=horizon,
        bot_seats=len(cfg.bot_seats()),
    )
    return recs, vor


def render(
    cfg: config.LeagueConfig,
    board: Sequence[pool.Item],
    state: BoardState,
    seat: int,
    recs: Sequence[optimizer.Recommendation],
    vor: dict[str, float],
) -> str:
    picks = cfg.pick_numbers(seat)
    current = state.picks_made + 1
    mine_now = current in set(picks)
    upcoming = [p for p in picks if p > current or (p == current and not mine_now)]
    rnd = (current - 1) // cfg.num_agents + 1
    roster_full = len(state.my_roster) >= cfg.rounds

    total = cfg.rounds * cfg.num_agents
    out = [BAR]
    if current > total:
        out.append("  DRAFT COMPLETE - all picks made")
        out.append(BAR)
        out.append(f"  ROSTER  {_slot_status(state.my_roster, cfg)}"
                   f"   ({len(state.my_roster)}/{cfg.rounds} taken)")
        gaps = [p for p, n in _unfilled(state.my_roster, cfg).items() if n]
        if gaps:
            out.append(f"  WARNING no {', '.join(gaps)} on the roster - add a free agent")
        out.append(BAR)
        return "\n".join(out)
    if roster_full:
        header = "DRAFT COMPLETE - your roster is full"
    elif mine_now:
        header = "YOUR PICK"
    else:
        header = f"waiting (seat {state.on_the_clock} on the clock)"
    out.append(f"  {header} - round {rnd}, overall pick {current}")
    out.append(BAR)

    if roster_full:
        out.append(f"  ROSTER  {_slot_status(state.my_roster, cfg)}"
                   f"   ({len(state.my_roster)}/{cfg.rounds} taken)")
        gaps = [p for p, n in _unfilled(state.my_roster, cfg).items() if n]
        if gaps:
            out.append(f"  WARNING no {', '.join(gaps)} on the roster - add a free agent")
        out.append(BAR)
        return "\n".join(out)

    if not recs:
        out.append("  no candidates available")
        out.append(BAR)
        return "\n".join(out)

    out.append(f"  {'#':<3}{'PLAYER':<24}{'POS':<5}{'VALUE':>8}{'ADP':>7}   {'EV':>8}")
    for rank, rec in enumerate(recs[:3], 1):
        item = rec.item
        mark = "  <= TAKE" if rank == 1 else ""
        out.append(
            f"  {rank:<3}{item.name[:23]:<24}{item.pos:<5}"
            f"{vor.get(item.player_id or item.name, 0.0):>8.0f}"
            f"{item.adp or 0:>7.0f}   {rec.expected_lineup_value:>8.0f}{mark}"
        )
    out.append("")

    top = recs[0]
    spread = top.expected_lineup_value - recs[min(2, len(recs) - 1)].expected_lineup_value
    stakes = "LOW - any of these is fine, decide fast" if spread < 8 else (
        "HIGH - the top choice is meaningfully better" if spread > 25 else "MEDIUM"
    )
    out.append(f"  STAKES  {stakes} (spread {spread:.0f} pts)")

    adp = top.item.adp or 0
    delta = adp - current
    if delta < -12:
        sanity = f"UNUSUAL - market ranks {top.item.name} {abs(delta):.0f} picks EARLIER"
    elif delta > 25:
        sanity = f"REACH - market ranks {top.item.name} {delta:.0f} picks later; you may be able to wait"
    else:
        sanity = f"OK - within {abs(delta):.0f} picks of market consensus"
    out.append(f"  SANITY  {sanity}")
    if mine_now:
        nxt = [p for p in picks if p > current]
        wait = f"next turn is pick {nxt[0]} ({nxt[0] - current} away)" if nxt else "this is your final pick"
    else:
        nxt = [p for p in picks if p >= current]
        wait = f"your next pick is {nxt[0]} ({nxt[0] - current} away)" if nxt else "no picks left"
    out.append(f"  WHY     {wait}; {len(upcoming)} of {cfg.rounds} picks remaining")
    out.append(f"  ROSTER  {_slot_status(state.my_roster, cfg)}"
               f"   ({len(state.my_roster)}/{cfg.rounds} taken)")
    out.append(BAR)
    return "\n".join(out)
