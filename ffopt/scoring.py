"""The payoff function.

An item's payoff is a linear function of its statistics:

    payoff = sum over k of  stats[k] * weight[k]

The forecast stat keys and the league's scoring-weight keys share a namespace,
so this is a plain dot product over their intersection. Stats with no
corresponding weight (consensus-order fields, games played, derived percentages)
carry zero weight by construction.
"""

from __future__ import annotations

from typing import Iterable, Mapping

# Stat keys that are metadata rather than scoreable production. These never
# appear in a scoring-weight vector, but are excluded explicitly so that a
# future league with an unusual weight key cannot silently pick them up.
NON_SCORING_KEYS = frozenset(
    {"gp", "cmp_pct", "pts_ppr", "pts_half_ppr", "pts_std"}
)


def is_scoring_key(key: str) -> bool:
    return not key.startswith("adp_") and key not in NON_SCORING_KEYS


def payoff(stats: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Dot product of realized/forecast stats with league scoring weights."""
    total = 0.0
    for key, value in stats.items():
        if not is_scoring_key(key):
            continue
        w = weights.get(key)
        if w:
            total += value * w
    return total


def unscored_keys(stats: Iterable[str], weights: Mapping[str, float]) -> set[str]:
    """Scoreable stat keys carrying no weight in this league.

    Useful as a diagnostic: a large or surprising set here means the league has
    a scoring quirk the forecast does not cover, or vice versa.
    """
    return {k for k in stats if is_scoring_key(k) and k not in weights}
