"""Versioned baseline and shadow-policy evaluation on identical frozen inputs.

These comparisons expose disagreements. They do not estimate realized benefit,
train on outcomes, promote a challenger, or submit any action to Sleeper.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
from typing import Protocol, Sequence

from . import archive, config, inseason, lineup


class Policy(Protocol):
    name: str
    version: str

    @property
    def parameters(self) -> dict: ...

    def recommend(self, cfg: config.LeagueConfig, state: inseason.WeekState) -> dict: ...


@dataclasses.dataclass(frozen=True)
class ExpectedPoints:
    name: str = "expected-points"
    version: str = "1"

    @property
    def parameters(self) -> dict:
        return {}

    def recommend(self, cfg: config.LeagueConfig, state: inseason.WeekState) -> dict:
        best = lineup.optimise_for(inseason.my_candidates(state), cfg)
        return {
            "lineup": [
                {
                    "slot_index": i, "slot": s.name,
                    "player_id": s.player.player_id if s.player else None,
                    "name": s.player.name if s.player else None,
                    "projected_points": s.player.points if s.player else None,
                    "locked": s.pinned,
                }
                for i, s in enumerate(best.slots)
            ],
            "projected_total": best.total,
            "changes": [dataclasses.asdict(c) for c in inseason.lineup_changes(state, best)],
            "pickups": [
                dataclasses.asdict(s) for s in inseason.stream_recommendations(state, cfg)
                if s.worth_doing
            ],
            "unfilled": best.unfilled,
            "excluded": [
                {"player_id": c.player_id, "reason": reason} for c, reason in best.excluded
            ],
            "warnings": list(state.move_warnings) + [
                f"{c.name}: {c.status}; verify official inactives" for c in best.flagged
            ],
            "assumptions": [
                "Point forecasts are estimates, not guaranteed scores.",
                "Pickup value covers this week only, not future roster or priority cost.",
                "Unowned-player claimability must be confirmed in Sleeper.",
            ],
        }


@dataclasses.dataclass(frozen=True)
class PickupFloor:
    """Demonstration challenger: a caller-specified churn threshold, not fitted."""

    minimum_gain: float
    name: str = "experimental-pickup-floor"
    version: str = "1"

    def __post_init__(self):
        if (
            isinstance(self.minimum_gain, bool) or not isinstance(self.minimum_gain, (float, int))
            or not math.isfinite(self.minimum_gain) or self.minimum_gain < 0
        ):
            raise ValueError("minimum pickup gain must be a finite nonnegative number")

    @property
    def parameters(self) -> dict:
        return {"minimum_gain": self.minimum_gain}

    def recommend(self, cfg: config.LeagueConfig, state: inseason.WeekState) -> dict:
        result = ExpectedPoints().recommend(cfg, state)
        result["pickups"] = [s for s in result["pickups"] if s["gain"] >= self.minimum_gain]
        result["assumptions"].append(
            "The minimum pickup gain is user supplied, uncalibrated, and not a modeled future cost."
        )
        return result


def _identity(policy: Policy) -> str:
    return f"{policy.name}@{policy.version}:{archive.digest(archive.encode(policy.parameters))[:12]}"


def compare(
    cfg: config.LeagueConfig, state: inseason.WeekState,
    challengers: Sequence[Policy] = (),
) -> dict:
    policies: list[Policy] = [ExpectedPoints(), *challengers]
    identities = [_identity(p) for p in policies]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate policy name/version/parameters")
    outputs = []
    for i, policy in enumerate(policies):
        # A future experimental policy must not contaminate another policy's inputs.
        result = policy.recommend(
            config.LeagueConfig(copy.deepcopy(cfg.raw)), copy.deepcopy(state)
        )
        # Keep the public report identical in memory, SQLite and a future JSON API.
        result = json.loads(archive.encode(result))
        outputs.append({
            "id": identities[i], "name": policy.name, "version": policy.version,
            "parameters": dict(policy.parameters),
            "role": "baseline" if i == 0 else "shadow-only",
            "recommendation": result,
        })
    baseline = outputs[0]["recommendation"]

    def assignment(result):
        return [(s["slot_index"], s["player_id"]) for s in result["lineup"]]

    def pickups(result):
        return sorted(
            (s["add"]["player_id"], s["drop"]["player_id"] if s["drop"] else "")
            for s in result["pickups"]
        )

    disagreements = []
    for output in outputs[1:]:
        result = output["recommendation"]
        disagreements.append({
            "challenger": output["id"],
            "lineup_changed": assignment(result) != assignment(baseline),
            "pickups_changed": pickups(result) != pickups(baseline),
            "projected_lineup_delta": result["projected_total"] - baseline["projected_total"],
        })
    return {
        "schema_version": 1, "snapshot_id": state.snapshot_id,
        "decision_at_ms": state.fetched_at_ms, "season": state.season, "week": state.week,
        "league_id": state.league_id, "roster_id": state.my_roster_id,
        "policies": outputs, "disagreements": disagreements,
        "interpretation": (
            "Same archived inputs and decision time; disagreements are not measured gains. "
            "No challenger is promoted and no real-world action is submitted."
        ),
    }
