"""Read-only, bounded swaps scored for a supplied projected week or scenario.

Callers must provide ONLY active, eligible, unlocked hypothetical rosters, with
complete projections in the league's scoring units (full PPR where configured).
Do not clear current locks merely to pass validation: build an explicit future
horizon instead. Reserve/taxi players and live post-kickoff scores are not offers.

Lineup assignment uses the passed cfg, including its dedicated and FLEX slots.
This is not rest-of-season valuation, acceptance prediction or title-odds analysis.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from numbers import Real

from . import config, lineup
from .lineup import Candidate


LIMITATION = (
    "Projected lineup points for the labeled week/scenario only, net of required "
    "drops; not rest-of-season impact, title odds or acceptance probabilities. "
    "Bench depth, future bye/injury coverage and longer-term drop costs are not "
    "valued. Projections must use the supplied league scoring rules. Pair search "
    "is bounded; platform approval and transaction-time eligibility still apply."
)


def _integer(value: object, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def trade_guard(cfg: config.LeagueConfig, week: int) -> str | None:
    """Return a public, explicit blocking reason, or None when trades are open.

    ``week`` is the transaction week, not necessarily the projection horizon.
    Unknown deadlines fail closed. The configured deadline week is inclusive.
    """
    _integer(week, "week", 1)
    if not cfg.trades_enabled:
        return "Trades are disabled or their enabled setting is missing."
    deadline = cfg.trade_deadline_week
    if deadline is None:
        return "Trade deadline is unknown; refresh league rules before searching."
    _integer(deadline, "trade deadline week", 1)
    if week > deadline:
        return f"Trade deadline has passed: week {week} > deadline week {deadline}."
    return None


@dataclasses.dataclass(frozen=True, slots=True)
class TeamImpact:
    before: float
    after: float
    gain: float
    drop_ids: tuple[str, ...]
    drop_cost: float
    before_unfilled: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class Trade:
    our_roster_id: int
    opponent_roster_id: int
    send_ids: tuple[str, ...]
    receive_ids: tuple[str, ...]
    ours: TeamImpact
    opponent: TeamImpact
    horizon_label: str
    limitation: str = LIMITATION


@dataclasses.dataclass(frozen=True, slots=True)
class TradeSearch:
    trades: tuple[Trade, ...]
    horizon_label: str
    reason: str | None = None
    warnings: tuple[str, ...] = ()
    candidate_limit: int = 6
    pairs_truncated: bool = False
    swaps_considered: int = 0
    valuations: int = 0
    limitation: str = LIMITATION


@dataclasses.dataclass(frozen=True, slots=True)
class _Value:
    total: float
    unfilled: tuple[str, ...]
    starters: frozenset[str]


def find_trades(
    rosters: dict[int, list[Candidate]],
    ours: int,
    cfg: config.LeagueConfig,
    *,
    week: int,
    horizon_label: str,
    candidate_limit: int = 6,
    max_results: int = 5,
) -> TradeSearch:
    """Find mutually improving 1:1, 2:1 and 1:2 player-only swaps.

    All singles are considered. Pairs use only the ``candidate_limit`` highest
    projected players per team (ties by ID); 0 or 1 disables pairs. At most five
    results are returned, sorted by our gain, then the opponent's. This pruning
    is explicit, not a claim to exhaustive two-for-one search.

    Rosters must be mutually exclusive and within cfg.roster_size. Incomplete,
    locked, reserve or ineligible player inputs raise ValueError, rather than
    silently inflating a gain by treating missing projections as zero.
    Incomplete starting lineups are reported; resulting lineups must be complete.

    Any recipient above capacity must drop one of its RETAINED original players,
    never an incoming player. The best legal drop is chosen by re-optimizing;
    both teams' gains include its cost. Input objects are never changed.
    """
    _integer(ours, "our roster ID", 1)
    _integer(candidate_limit, "candidate_limit")
    _integer(max_results, "max_results", 1)
    if max_results > 5:
        raise ValueError("max_results must be in 1..5")
    if not isinstance(horizon_label, str) or not horizon_label.strip():
        raise ValueError("horizon_label must explicitly name the projected week/scenario")
    if reason := trade_guard(cfg, week):
        return TradeSearch(
            (), horizon_label, reason=reason, candidate_limit=candidate_limit,
        )
    capacity = _integer(cfg.roster_size, "active roster capacity", 1)
    slots = cfg.starting_positions
    if not slots or len(slots) > capacity:
        raise ValueError("starting slots must be nonempty and fit active roster capacity")
    if not isinstance(rosters, dict) or ours not in rosters:
        raise ValueError("rosters must contain our roster ID")
    all_players: dict[str, Candidate] = {}
    teams: dict[int, frozenset[str]] = {}
    allowed_positions = (set(slots) - {config.FLEX_SLOT}) | set(cfg.flex_types)
    for roster_id, players in rosters.items():
        _integer(roster_id, "roster ID", 1)
        if not isinstance(players, (list, tuple)):
            raise ValueError(f"roster {roster_id} must be a list of candidates")
        if len(players) > capacity:
            raise ValueError(f"roster {roster_id} exceeds active roster capacity")
        ids = []
        for player in players:
            if not isinstance(player, Candidate):
                raise ValueError(f"roster {roster_id} contains a non-Candidate player")
            pid = player.player_id
            if not isinstance(pid, str) or not pid.strip() or pid != pid.strip() or pid == "0":
                raise ValueError(f"roster {roster_id} has an invalid player ID")
            if pid in all_players:
                raise ValueError(f"duplicate player {pid}: rosters must be exclusive")
            if player.locked:
                raise ValueError(
                    f"roster {roster_id}, player {pid} is locked; "
                    "supply an explicitly unlocked future-horizon roster"
                )
            try:
                finite_projection = (
                    not isinstance(player.points, bool)
                    and isinstance(player.points, Real)
                    and math.isfinite(player.points)
                )
            except OverflowError:
                finite_projection = False
            if not finite_projection:
                raise ValueError(f"roster {roster_id}, player {pid}: missing/non-finite projection")
            if reason := lineup.ineligibility(player):
                raise ValueError(f"roster {roster_id}, player {pid} is ineligible: {reason}")
            positions = player.positions or (player.pos,)
            if (
                not isinstance(positions, (tuple, list))
                or any(not isinstance(p, str) for p in positions)
                or not set(positions) & allowed_positions
            ):
                raise ValueError(f"roster {roster_id}, player {pid}: no eligible position")
            all_players[pid] = player
            ids.append(pid)
        teams[roster_id] = frozenset(ids)

    cache: dict[frozenset[str], _Value] = {}

    def value(ids: frozenset[str]) -> _Value:
        if ids not in cache:
            result = lineup.optimise_for(
                (all_players[pid] for pid in sorted(ids)), cfg
            )
            if result.excluded:
                raise ValueError("lineup optimizer excluded a supplied roster candidate")
            if not math.isfinite(result.total):
                raise ValueError("projected lineup total exceeds finite numeric range")
            cache[ids] = _Value(
                result.total, tuple(result.unfilled),
                frozenset(p.player_id for p in result.starters()),
            )
        return cache[ids]

    def post_trade(
        original: frozenset[str], outgoing: tuple[str, ...], incoming: tuple[str, ...],
        before: _Value,
    ) -> tuple[_Value, tuple[str, ...], float] | None:
        retained = original - frozenset(outgoing)
        acquired = frozenset(incoming)
        ids = retained | acquired
        augmented = value(original | acquired)
        if augmented.unfilled or augmented.total <= before.total:
            return None
        # Reuse the optimal superset when the offered players are not starters.
        if augmented.starters <= ids:
            cache[ids] = augmented
        uncapped = value(ids)
        excess = len(ids) - capacity
        if excess <= 0:
            return uncapped, (), 0.0
        if excess != 1 or not retained:
            return None
        # Removing an original bench player preserves the exact optimal lineup.
        bench_drops = sorted(retained - uncapped.starters)
        if bench_drops:
            drop = bench_drops[0]
            cache[ids - {drop}] = uncapped
            return uncapped, (drop,), 0.0
        choices = [(value(ids - {pid}), pid) for pid in sorted(retained)]
        best, drop = max(choices, key=lambda item: (-len(item[0].unfilled), item[0].total))
        return best, (drop,), uncapped.total - best.total

    baseline = {rid: value(ids) for rid, ids in teams.items()}
    warnings = [
        f"Roster {rid} baseline has unfilled starting slots: {', '.join(v.unfilled)}."
        for rid, v in sorted(baseline.items()) if v.unfilled
    ]
    pairs_truncated = candidate_limit >= 2 and any(
        len(ids) > candidate_limit for ids in teams.values()
    )
    if pairs_truncated:
        warnings.append(
            f"Pairs restricted to the top {candidate_limit} projected players per roster; "
            "other two-for-one trades were not searched."
        )
    if candidate_limit < 2:
        warnings.append("Pair search disabled; only one-for-one trades were searched.")
    singles = {rid: tuple((pid,) for pid in sorted(ids)) for rid, ids in teams.items()}
    pairs = {
        rid: tuple(itertools.combinations(sorted(
            ids, key=lambda pid: (-all_players[pid].points, pid)
        )[:candidate_limit], 2))
        for rid, ids in teams.items()
    }
    considered, proposals = 0, []

    def can_improve(ids: frozenset[str], before: _Value) -> bool:
        # An optimistic bound ignoring positional constraints cannot hide a gain.
        if len(ids) < len(slots):
            return False
        best_points = sorted((all_players[pid].points for pid in ids), reverse=True)
        return sum(best_points[:len(slots)]) > before.total

    def impact(
        before: _Value, after: _Value, drops: tuple[str, ...], drop_cost: float,
    ) -> TeamImpact:
        gain = after.total - before.total
        if not math.isfinite(gain) or not math.isfinite(drop_cost):
            raise ValueError("projected gain/drop cost exceeds finite numeric range")
        return TeamImpact(
            before.total, after.total, gain, drops, drop_cost, before.unfilled,
        )

    for opponent_id in sorted(teams):
        if opponent_id == ours:
            continue
        combinations = itertools.chain(
            itertools.product(singles[ours], singles[opponent_id]),
            itertools.product(pairs[ours], singles[opponent_id]),
            itertools.product(singles[ours], pairs[opponent_id]),
        )
        for send, receive in combinations:
            considered += 1
            our_ids = (teams[ours] - frozenset(send)) | frozenset(receive)
            their_ids = (teams[opponent_id] - frozenset(receive)) | frozenset(send)
            if not (
                can_improve(our_ids, baseline[ours])
                and can_improve(their_ids, baseline[opponent_id])
            ):
                continue
            our_result = post_trade(teams[ours], send, receive, baseline[ours])
            if our_result is None:
                continue
            our_value, our_drops, our_drop_cost = our_result
            if our_value.unfilled or our_value.total <= baseline[ours].total:
                continue
            their_result = post_trade(
                teams[opponent_id], receive, send, baseline[opponent_id],
            )
            if their_result is None:
                continue
            their_value, their_drops, their_drop_cost = their_result
            if their_value.unfilled or their_value.total <= baseline[opponent_id].total:
                continue
            proposals.append(Trade(
                ours, opponent_id, tuple(sorted(send)), tuple(sorted(receive)),
                impact(baseline[ours], our_value, our_drops, our_drop_cost),
                impact(baseline[opponent_id], their_value, their_drops, their_drop_cost),
                horizon_label,
            ))
    proposals.sort(key=lambda trade: (
        -trade.ours.gain, -trade.opponent.gain, trade.opponent_roster_id,
        trade.send_ids, trade.receive_ids,
    ))
    return TradeSearch(
        tuple(proposals[:max_results]), horizon_label,
        reason=None if proposals else "No mutually improving legal swaps in the searched candidates.",
        warnings=tuple(warnings), candidate_limit=candidate_limit,
        pairs_truncated=pairs_truncated, swaps_considered=considered,
        valuations=len(cache),
    )
