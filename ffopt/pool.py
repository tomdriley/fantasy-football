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


def short_name(name: str, pos: str) -> str:
    """"Jahmyr Gibbs" -> "J. Gibbs", matching how the platform lists picks.

    Display only -- searching and matching still use the full name. The point
    is recognition speed: during a draft the operator is comparing what is on
    this screen against what the platform shows, and two differently formatted
    names take measurably longer to match than two identical ones.

    Three cases are left alone because abbreviating them would lose the part
    that identifies the player:

    * A first name that is already an initialism ("A.J. Brown", "T.J.
      Hockenson"). "A. Brown" is not shorter in any useful sense and there are
      several Browns.
    * Multi-token surnames ("Amon-Ra St. Brown" -> "A. St. Brown"): everything
      after the first token is kept.
    * Team defenses, which have no personal name at all. The nickname alone is
      how they are listed and is unique across the league.
    """
    if pos == "DEF":
        parts = name.split()
        return parts[-1] if parts else name
    parts = name.split()
    if len(parts) < 2:
        return name
    first, rest = parts[0], " ".join(parts[1:])
    if "." in first:
        return name
    return f"{first[0]}. {rest}"


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
