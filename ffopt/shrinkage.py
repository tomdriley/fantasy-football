"""Shrinkage of forecast payoffs toward a market-implied prior.

The backtest exposed a large, systematic selection bias: players chosen by
maximising expected value underperformed their projections by 52 points on
average, against 20 points for a strategy that simply followed market consensus.
Across fifteen picks that is roughly 480 points of pure selection error, more
than enough to explain losing to the platform default.

This is the optimizer's curse. Forecasts carry noise. Selecting the argmax over
noisy estimates preferentially selects items whose noise happens to be positive,
so the winner's realized value is systematically below its estimate -- and the
harder the search optimises, the worse the bias. A strategy that follows
consensus order is immune, because its choice is uncorrelated with the
forecast's individual errors. That is why a *better* predictor (projections beat
consensus at Spearman +0.55 against +0.38) can still produce *worse* decisions.

The standard remedy is to shrink each estimate toward a prior in proportion to
how noisy it is. Here the prior is market-implied: for an item at consensus rank
k within its type, the prior is the smoothed forecast of items around rank k.
That strips out an individual forecast's idiosyncratic error while keeping the
market's ordering information, which is exactly the part that survives contact
with reality.

    adjusted = (1 - lambda) * forecast + lambda * market_prior

lambda = 0 trusts the forecast completely and suffers the full curse. lambda = 1
discards individual forecasts and follows the smoothed market. The useful
setting is empirical and is chosen by backtest.
"""

from __future__ import annotations

import dataclasses
from typing import Sequence

from . import pool

#: Neighbours either side of an item when smoothing the market prior.
DEFAULT_WINDOW = 3

#: Shrinkage weight toward the market prior. Chosen by backtest; see docs.
DEFAULT_LAMBDA = 0.5


def market_prior(
    items: Sequence[pool.Item], window: int = DEFAULT_WINDOW
) -> dict[str, float]:
    """Smoothed forecast payoff by consensus rank, computed within each type.

    Items without a consensus rank keep their own forecast: there is no market
    signal to shrink them toward.
    """
    by_pos: dict[str, list[pool.Item]] = {}
    for item in items:
        if item.adp is not None:
            by_pos.setdefault(item.pos, []).append(item)

    prior: dict[str, float] = {}
    for group in by_pos.values():
        group.sort(key=lambda i: i.adp)
        payoffs = [i.payoff for i in group]
        for rank, item in enumerate(group):
            lo = max(0, rank - window)
            hi = min(len(payoffs), rank + window + 1)
            neighbourhood = payoffs[lo:hi]
            prior[item.player_id or item.name] = sum(neighbourhood) / len(neighbourhood)
    return prior


def shrink(
    items: Sequence[pool.Item],
    lam: float = DEFAULT_LAMBDA,
    window: int = DEFAULT_WINDOW,
) -> list[pool.Item]:
    """Return items with payoffs shrunk toward the market-implied prior."""
    if not 0.0 <= lam <= 1.0:
        raise ValueError("lam must be in [0, 1]")
    if lam == 0.0:
        return list(items)
    prior = market_prior(items, window)
    out: list[pool.Item] = []
    for item in items:
        key = item.player_id or item.name
        target = prior.get(key)
        payoff = item.payoff if target is None else (1 - lam) * item.payoff + lam * target
        out.append(dataclasses.replace(item, payoff=payoff))
    out.sort(key=lambda i: -i.payoff)
    return out
