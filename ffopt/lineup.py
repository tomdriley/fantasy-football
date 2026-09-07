"""Exact weekly assignment, preserving each locked player's original slot.

The objective is lexicographic: fill as many legal slots as possible, then
maximize projected points. Equal-value assignments keep later games in FLEX
where possible, preserving substitution options without sacrificing points.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Iterable, Sequence

from . import config

HARD_OUT = frozenset({"IR", "PUP", "Out", "Sus", "NA", "DNR", "COV"})
DOUBTFUL = frozenset({"Doubtful"})
FLAGGED = frozenset({"Questionable", "Doubtful", "Probable"})


@dataclasses.dataclass(slots=True)
class Candidate:
    player_id: str
    name: str
    pos: str
    points: float | None = None
    status: str | None = None
    on_bye: bool = False
    locked: bool = False
    started: bool = False
    current_slot: int | None = None
    kickoff_ms: int | None = None
    unavailable_reason: str | None = None
    positions: tuple[str, ...] = ()

    @property
    def flagged(self) -> bool:
        return self.status in FLAGGED


@dataclasses.dataclass(slots=True)
class Slot:
    name: str
    player: Candidate | None = None
    pinned: bool = False

    @property
    def filled(self) -> bool:
        return self.player is not None


@dataclasses.dataclass(slots=True)
class Lineup:
    slots: list[Slot]
    bench: list[Candidate]
    excluded: list[tuple[Candidate, str]]

    @property
    def total(self) -> float:
        return sum(s.player.points or 0.0 for s in self.slots if s.player)

    @property
    def unfilled(self) -> list[str]:
        return [s.name for s in self.slots if not s.filled]

    @property
    def flagged(self) -> list[Candidate]:
        return [s.player for s in self.slots if s.player and s.player.flagged]

    def starters(self) -> list[Candidate]:
        return [s.player for s in self.slots if s.player]


def ineligibility(candidate: Candidate, *, allow_doubtful: bool = False) -> str | None:
    if candidate.on_bye:
        return "bye week"
    if candidate.unavailable_reason:
        return candidate.unavailable_reason
    if candidate.status in HARD_OUT:
        return f"status {candidate.status}"
    if candidate.status in DOUBTFUL and not allow_doubtful:
        return f"status {candidate.status}"
    if candidate.points is None:
        return "missing projection"
    return None


def optimise(
    candidates: Iterable[Candidate],
    *,
    slot_names: Sequence[str],
    flex_types: Sequence[str],
    flex_slot: str = config.FLEX_SLOT,
    allow_doubtful: bool = False,
) -> Lineup:
    everyone = list(candidates)
    ids = [c.player_id for c in everyone]
    if len(set(ids)) != len(ids) or any(not p or p == "0" for p in ids):
        raise ValueError("candidate player IDs must be unique and nonempty")
    supported = set(config.SCORING_TYPES) | {flex_slot}
    if not slot_names or any(s not in supported for s in slot_names):
        raise ValueError("unsupported or empty starting slot configuration")
    for c in everyone:
        if c.points is not None and not math.isfinite(c.points):
            raise ValueError(f"non-finite projection for {c.player_id}")

    def accepts(c: Candidate, slot: str) -> bool:
        positions = set(c.positions or (c.pos,))
        return bool(positions & set(flex_types)) if slot == flex_slot else slot in positions

    slots = [Slot(name) for name in slot_names]
    excluded: list[tuple[Candidate, str]] = []
    eligible: list[Candidate] = []
    for c in everyone:
        if c.locked and c.started:
            index = c.current_slot
            if index is None or not 0 <= index < len(slots):
                raise ValueError(f"locked starter {c.player_id} has no valid original slot")
            if slots[index].filled or not accepts(c, slots[index].name):
                raise ValueError(f"invalid locked assignment for {c.player_id}")
            # Even an injured locked starter cannot be removed after kickoff.
            slots[index].player, slots[index].pinned = c, True
        elif c.locked:
            excluded.append((c, "locked on bench"))
        elif reason := ineligibility(c, allow_doubtful=allow_doubtful):
            excluded.append((c, reason))
        else:
            eligible.append(c)

    open_indices = [i for i, s in enumerate(slots) if not s.filled]
    # One state per occupied-slot bitmask; each player is considered only once.
    states: dict[int, tuple[float, int, int, tuple[tuple[int, Candidate], ...]]] = {
        0: (0.0, 0, 0, ())
    }
    for c in sorted(eligible, key=lambda p: p.player_id):
        for mask, (total, flexibility, unchanged, assignment) in list(states.items()):
            for bit, index in enumerate(open_indices):
                if mask & (1 << bit) or not accepts(c, slots[index].name):
                    continue
                new_mask = mask | (1 << bit)
                score = (
                    total + (c.points or 0.0),
                    flexibility + (
                        (c.kickoff_ms or 0) if slots[index].name == flex_slot else 0
                    ),
                    unchanged + int(c.current_slot == index),
                )
                previous = states.get(new_mask)
                if previous is None or score > previous[:3]:
                    states[new_mask] = (*score, assignment + ((index, c),))
    mask = max(states, key=lambda m: (m.bit_count(), *states[m][:3]))
    for index, c in states[mask][3]:
        slots[index].player = c
    started_ids = {s.player.player_id for s in slots if s.player}
    return Lineup(
        slots, [c for c in eligible if c.player_id not in started_ids], excluded
    )


def optimise_for(candidates: Iterable[Candidate], cfg: config.LeagueConfig, **kwargs) -> Lineup:
    return optimise(
        candidates, slot_names=cfg.starting_positions, flex_types=cfg.flex_types, **kwargs
    )
