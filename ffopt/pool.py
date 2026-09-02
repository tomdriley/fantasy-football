"""The item pool: players with a type, a forecast payoff, and a consensus order."""

from __future__ import annotations

import dataclasses
from typing import Iterable, Sequence

from . import config, scoring

ADP_FIELD = "adp_ppr"


@dataclasses.dataclass(slots=True)
class Item:
    """A draftable player."""

    player_id: str
    name: str
    pos: str
    team: str | None
    payoff: float
    adp: float | None
    games: float
    bye: int | None = None
    injury: str | None = None

    @property
    def has_consensus(self) -> bool:
        return self.adp is not None


def _name(player: dict, record: dict) -> str:
    first = (player.get("first_name") or "").strip()
    last = (player.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    return full or str(record.get("team") or player.get("player_id") or "?")


def build(
    records: Iterable[dict],
    weights: dict[str, float],
    *,
    require_games: bool = True,
    adp_field: str = ADP_FIELD,
) -> list[Item]:
    """Convert raw projection/stat records into scored items.

    `require_games` drops records with no projected games played; those are
    retired, injured-out, or otherwise inactive entries that would otherwise
    pollute replacement-level computation with zeros.
    """
    items: list[Item] = []
    for rec in records:
        stats = rec.get("stats") or {}
        player = rec.get("player") or {}
        positions = player.get("fantasy_positions") or []
        pos = positions[0] if positions else None
        if pos not in config.SCORING_TYPES:
            continue
        if require_games and not stats.get("gp"):
            continue
        items.append(
            Item(
                player_id=str(rec.get("player_id") or player.get("player_id") or ""),
                name=_name(player, rec),
                pos=pos,
                team=rec.get("team"),
                payoff=scoring.payoff(stats, weights),
                adp=stats.get(adp_field),
                games=float(stats.get("gp") or 0.0),
                bye=player.get("bye_week"),
                injury=player.get("injury_status"),
            )
        )
    items.sort(key=lambda i: -i.payoff)
    return items


def by_position(items: Sequence[Item]) -> dict[str, list[Item]]:
    """Items grouped by type, each group sorted by descending payoff."""
    groups: dict[str, list[Item]] = {p: [] for p in config.SCORING_TYPES}
    for item in items:
        groups.setdefault(item.pos, []).append(item)
    for group in groups.values():
        group.sort(key=lambda i: -i.payoff)
    return groups
