"""Opponent model: how the other 9 agents consume the item pool.

Two opponent types:

* **Deterministic (autopick).** Empty seats claim the best remaining item by
  consensus order, every time. Zero variance. Up to 4 of 9 opponents here.
* **Human.** Claims near the top of consensus order but "reaches" past it
  sometimes. Modelled as taking the j-th best available item where j is
  geometric, so j=0 (strict consensus) is most likely and larger reaches decay
  geometrically.

The reach parameter is NOT measurable from the platform: every published ADP
field is a mean for a different scoring format, and no dispersion is exposed.
(`adp_std` is standard-*scoring* ADP, not a standard deviation.) Attempts to
calibrate from real completed drafts found none reachable. It is therefore a
free parameter, and the correct response is to show decisions are stable across
its plausible range rather than to invent a precise value.
"""

from __future__ import annotations

import random
from typing import Sequence

from . import pool

# Mean number of positions an average human opponent "reaches" past the best
# available item by consensus order. 0 reproduces strict consensus ordering.
DEFAULT_REACH = 1.5


class OpponentModel:
    """Samples which board index an opponent claims."""

    def __init__(self, reach: float = DEFAULT_REACH, rng: random.Random | None = None):
        if reach < 0:
            raise ValueError("reach must be non-negative")
        self.reach = reach
        self.rng = rng or random.Random()

    def _sample_offset(self, deterministic: bool) -> int:
        if deterministic or self.reach <= 0:
            return 0
        # Geometric with mean `reach`: P(j) = (1-p)^j * p, mean = (1-p)/p
        p = 1.0 / (1.0 + self.reach)
        j = 0
        while self.rng.random() > p:
            j += 1
            if j > 40:  # guard against pathological tails
                break
        return j

    def claim(self, available: Sequence[int], deterministic: bool = False) -> int:
        """Return the board index claimed from `available` (consensus-ordered)."""
        if not available:
            raise ValueError("no items available")
        offset = min(self._sample_offset(deterministic), len(available) - 1)
        return available[offset]


def consensus_order(items: Sequence[pool.Item]) -> list[pool.Item]:
    """Items sorted by consensus claim order; items without consensus go last."""
    with_adp = [i for i in items if i.adp is not None]
    without = [i for i in items if i.adp is None]
    with_adp.sort(key=lambda i: i.adp)
    without.sort(key=lambda i: -i.payoff)
    return with_adp + without


def survival_probabilities(
    board: Sequence[pool.Item],
    gap: int,
    *,
    num_bots: int = 0,
    reach: float = DEFAULT_REACH,
    trials: int = 500,
    rng: random.Random | None = None,
) -> dict[str, float]:
    """P(each item is still unclaimed after `gap` opponent claims).

    `board` must already be in consensus order.
    """
    rng = rng or random.Random()
    model = OpponentModel(reach=reach, rng=rng)
    counts = [0] * len(board)
    bot_flags = [True] * num_bots + [False] * max(gap - num_bots, 0)
    for _ in range(trials):
        alive = list(range(len(board)))
        flags = bot_flags[:gap]
        rng.shuffle(flags)
        for is_bot in flags:
            if not alive:
                break
            idx = model.claim(alive, deterministic=is_bot)
            alive.remove(idx)
        for i in alive:
            counts[i] += 1
    return {board[i].player_id or board[i].name: c / trials for i, c in enumerate(counts)}
