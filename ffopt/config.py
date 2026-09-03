"""League configuration, loaded from the committed YAML source of truth.

docs/league-rules.yaml is generated from the platform API and is the single place
league rules live. Nothing here re-types a rule by hand.
"""

from __future__ import annotations

import functools
import pathlib
from typing import Any

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RULES_PATH = REPO_ROOT / "docs" / "league-rules.yaml"

FLEX_SLOT = "FLEX"
SCORING_TYPES = ("QB", "RB", "WR", "TE", "K", "DEF")


class LeagueConfig:
    """Typed accessor over the rules YAML."""

    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    # -- identity -------------------------------------------------------
    @property
    def league_id(self) -> str:
        return self.raw["league"]["league_id"]

    @property
    def season(self) -> str:
        return self.raw["league"]["season"]

    @property
    def num_agents(self) -> int:
        return self.raw["league"]["total_agents"]

    @property
    def draft_id(self) -> str:
        return self.raw["draft"]["draft_id"]

    @property
    def my_user_id(self) -> str:
        return self.raw["my_team"]["user_id"]

    # -- payoff ---------------------------------------------------------
    @property
    def scoring_weights(self) -> dict[str, float]:
        return dict(self.raw["scoring_weights"])

    # -- roster constraints ---------------------------------------------
    @property
    def starting_slots(self) -> dict[str, int]:
        """Slots per agent, e.g. {'QB':1,'RB':2,...,'FLEX':2}."""
        return dict(self.raw["roster_constraints"]["starting_slots"])

    @property
    def dedicated_slots(self) -> dict[str, int]:
        """Starting slots excluding the wildcard FLEX slots."""
        return {k: v for k, v in self.starting_slots.items() if k != FLEX_SLOT}

    @property
    def flex_slots(self) -> int:
        return self.starting_slots.get(FLEX_SLOT, 0)

    @property
    def flex_types(self) -> list[str]:
        return list(self.raw["roster_constraints"]["flex_accepts_types"])

    @property
    def bench_slots(self) -> int:
        return self.raw["roster_constraints"]["bench_slots"]

    @property
    def roster_size(self) -> int:
        return self.raw["roster_constraints"]["total_roster_size"]

    # -- draft ----------------------------------------------------------
    @property
    def rounds(self) -> int:
        return self.raw["draft"]["rounds"]

    @property
    def pick_timer_seconds(self) -> int:
        return self.raw["draft"]["pick_timer_seconds"]

    @property
    def is_snake(self) -> bool:
        return self.raw["draft"]["type"] == "snake"

    def pick_numbers(self, seat: int) -> list[int]:
        """Overall pick indices (1-based) for `seat` under snake ordering.

        Odd rounds run 1..N; even rounds reverse. Seat is 1-based.
        """
        if not 1 <= seat <= self.num_agents:
            raise ValueError(f"seat must be in 1..{self.num_agents}, got {seat}")
        if not self.is_snake:
            raise NotImplementedError("only snake drafts are supported")
        n = self.num_agents
        picks = []
        for rnd in range(1, self.rounds + 1):
            offset = seat if rnd % 2 == 1 else (n + 1 - seat)
            picks.append((rnd - 1) * n + offset)
        return picks

    def seat_of_pick(self, pick_no: int) -> int:
        """Which draft slot is on the clock at 1-based overall `pick_no`.

        Inverse of `pick_numbers`. Odd rounds run 1..N, even rounds reverse.
        """
        n = self.num_agents
        rnd, offset = divmod(pick_no - 1, n)
        return offset + 1 if rnd % 2 == 0 else n - offset

    def pick_label(self, pick_no: int) -> str:
        """Format a pick the way the platform labels it: "9.5".

        The platform numbers picks *within* a round, not overall, so its board
        shows 9.5 where we would say pick 85. Displaying our own numbering
        makes the two impossible to compare at a glance -- and comparing them
        is the only cheap way for the operator to notice that our board has
        drifted out of step with the room, which silently invalidates every
        recommendation after it.

        Note this is a position in the round, not a seat: even rounds run
        backwards, so round 2 position 6 is seat 5.
        """
        rnd, offset = divmod(pick_no - 1, self.num_agents)
        return f"{rnd + 1}.{offset + 1}"

    def bot_seats(self) -> list[int]:
        """Draft slots owned by nobody, which run deterministic autopick.

        Only meaningful once draft order is assigned; before that the
        slot->roster mapping is provisional.
        """
        owners = {a["roster_id"]: a["user_id"] for a in self.raw["agents"]}
        s2r = self.raw["draft"].get("slot_to_roster_id") or {}
        return sorted(int(s) for s, rid in s2r.items() if not owners.get(rid))

    @property
    def draft_order_assigned(self) -> bool:
        return bool(self.raw["draft"].get("draft_order_assigned"))


@functools.lru_cache(maxsize=1)
def load(path: str | pathlib.Path | None = None) -> LeagueConfig:
    p = pathlib.Path(path) if path else RULES_PATH
    with open(p) as f:
        return LeagueConfig(yaml.safe_load(f))
