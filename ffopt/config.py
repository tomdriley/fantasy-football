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
    def starting_positions(self) -> list[str]:
        return [
            s for s in self.raw["roster_constraints"]["roster_positions_ordered"]
            if s != "BN"
        ]

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

    # -- in-season ------------------------------------------------------
    @property
    def in_season(self) -> dict[str, Any]:
        """Rules governing in-season actions, as captured from the API.

        Absent from rules files generated before the season started, so callers
        get an empty mapping rather than a KeyError.
        """
        return self.raw.get("in_season") or {}

    @property
    def is_in_season(self) -> bool:
        return self.raw["league"].get("status") == "in_season"

    @property
    def waiver_type(self) -> str:
        """'rolling_priority' | 'reverse_standings' | 'faab' | 'unknown'."""
        return (self.in_season.get("waivers") or {}).get("type", "unknown")

    @property
    def waiver_budget_is_active(self) -> bool:
        """True only under FAAB. A non-zero budget is inert otherwise.

        The league exposes waiver_budget: 100 while running rolling priority,
        which reads as a spendable resource and is not one.
        """
        return bool((self.in_season.get("waivers") or {}).get("budget_is_active"))

    @property
    def waiver_clear_days(self) -> int | None:
        return (self.in_season.get("waivers") or {}).get("clear_days")

    @property
    def trades_enabled(self) -> bool:
        return bool((self.in_season.get("trades") or {}).get("enabled"))

    @property
    def trade_deadline_week(self) -> int | None:
        return (self.in_season.get("trades") or {}).get("deadline_week")

    @property
    def draft_pick_trading(self) -> bool:
        return bool((self.in_season.get("trades") or {}).get("draft_pick_trading"))

    @property
    def reserve_slots(self) -> int:
        return (self.in_season.get("reserve") or {}).get("slots") or 0

    @property
    def reserve_extra_status_flags_enabled(self) -> bool:
        return bool((self.in_season.get("reserve") or {}).get("extra_status_flags_enabled"))

    @property
    def playoff_week_start(self) -> int | None:
        return self.raw["season_structure"].get("playoff_week_start")

    @property
    def playoff_teams(self) -> int | None:
        return self.raw["season_structure"].get("playoff_teams")

    @property
    def regular_season_weeks(self) -> int:
        """Last week that counts toward seeding, i.e. playoff_week_start - 1."""
        start = self.playoff_week_start
        return (start - 1) if start else 0

    def is_playoff_week(self, week: int) -> bool:
        start = self.playoff_week_start
        return bool(start and week >= start)


@functools.lru_cache(maxsize=1)
def load(path: str | pathlib.Path | None = None) -> LeagueConfig:
    return read(path)


def read(path: str | pathlib.Path | None = None) -> LeagueConfig:
    """Read uncached rules for long-lived service operations."""
    p = pathlib.Path(path) if path else RULES_PATH
    with open(p) as f:
        return LeagueConfig(yaml.safe_load(f))
