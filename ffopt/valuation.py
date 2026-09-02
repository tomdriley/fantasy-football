"""Value over replacement, and tier detection.

Because unfielded items score zero, an item's worth is not its payoff but its
payoff minus that of the best freely-available substitute of the same type --
its replacement level.

Replacement level depends on how many items of each type the league actually
consumes, which in turn depends on how the wildcard (FLEX) slots get filled.
Those slots do not distribute evenly: allocating them greedily by payoff, they
are absorbed overwhelmingly by one type, which materially moves the baselines.

IMPORTANT: value over replacement is an *input* to the claim decision, never the
claim order itself. It measures how much better than replacement an item is,
with no notion of when that item can be obtained. Sorted directly it ranks
kickers and defenses roughly 70 positions too high, because a kicker's small
positive value still exceeds the negative value of a deep receiver. Supplying
the missing "when" dimension is the optimizer's job.
"""

from __future__ import annotations

import dataclasses
from typing import Sequence

from . import config, pool


@dataclasses.dataclass(slots=True)
class Baselines:
    replacement: dict[str, float]
    consumed: dict[str, int]
    flex_absorption: dict[str, int]


def compute_baselines(
    items: Sequence[pool.Item], cfg: config.LeagueConfig
) -> Baselines:
    """Replacement payoff per type, accounting for wildcard slot absorption."""
    groups = pool.by_position(items)
    n = cfg.num_agents

    consumed = {pos: cnt * n for pos, cnt in cfg.dedicated_slots.items()}

    # Wildcard slots go to the best remaining items of any flex-eligible type.
    flex_total = cfg.flex_slots * n
    leftovers: list[pool.Item] = []
    for pos in cfg.flex_types:
        leftovers.extend(groups.get(pos, [])[consumed.get(pos, 0):])
    leftovers.sort(key=lambda i: -i.payoff)
    absorbed = leftovers[:flex_total]

    flex_absorption = {pos: 0 for pos in cfg.flex_types}
    for item in absorbed:
        flex_absorption[item.pos] = flex_absorption.get(item.pos, 0) + 1

    replacement: dict[str, float] = {}
    for pos in config.SCORING_TYPES:
        group = groups.get(pos, [])
        idx = consumed.get(pos, 0) + flex_absorption.get(pos, 0)
        consumed[pos] = idx
        # The replacement item is the next one after everything the league consumes.
        replacement[pos] = group[idx].payoff if len(group) > idx else 0.0

    return Baselines(
        replacement=replacement, consumed=consumed, flex_absorption=flex_absorption
    )


def value_over_replacement(item: pool.Item, baselines: Baselines) -> float:
    return item.payoff - baselines.replacement.get(item.pos, 0.0)


def tiers(
    items: Sequence[pool.Item], baselines: Baselines, *, max_tiers: int = 6
) -> dict[str, list[list[pool.Item]]]:
    """Cluster each type into value plateaus by finding the largest payoff gaps.

    Tiers are what make a draft plan robust: "claim the best available item from
    the highest live tier" reacts to the board rather than predicting it, so it
    is invariant to seat and to opponent behaviour.
    """
    out: dict[str, list[list[pool.Item]]] = {}
    for pos, group in pool.by_position(items).items():
        positive = [i for i in group if value_over_replacement(i, baselines) > 0]
        if len(positive) < 2:
            out[pos] = [positive] if positive else []
            continue
        gaps = sorted(
            ((positive[i].payoff - positive[i + 1].payoff, i) for i in range(len(positive) - 1)),
            reverse=True,
        )
        cuts = sorted(i for _, i in gaps[: max_tiers - 1])
        result, prev = [], 0
        for cut in cuts + [len(positive) - 1]:
            chunk = positive[prev : cut + 1]
            if chunk:
                result.append(chunk)
            prev = cut + 1
        out[pos] = result
    return out
