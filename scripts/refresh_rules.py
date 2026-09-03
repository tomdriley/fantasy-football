#!/usr/bin/env python3
"""Regenerate docs/league-rules.yaml from the live API.

The YAML is the single source of truth for every downstream component, so it
must reflect reality on draft day. League state changes right up to the start:
managers join, the draft order is assigned, keepers get declared. Running this
before the draft ensures the tool is not reasoning about last week's league.

    python3 scripts/refresh_rules.py          # rewrite the file
    python3 scripts/refresh_rules.py --check  # report drift, change nothing
"""
import argparse
import collections
import datetime
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import yaml

from ffopt import config

BASE = "https://api.sleeper.app/v1"


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def build(league_id: str) -> dict:
    endpoints = {
        "league": f"{BASE}/league/{league_id}",
        "users": f"{BASE}/league/{league_id}/users",
        "rosters": f"{BASE}/league/{league_id}/rosters",
        "state": f"{BASE}/state/nfl",
    }
    raw = {k: get(v) for k, v in endpoints.items()}
    league = raw["league"]
    draft_id = league["draft_id"]
    endpoints["draft"] = f"{BASE}/draft/{draft_id}"
    endpoints["picks"] = f"{BASE}/draft/{draft_id}/picks"
    raw["draft"] = get(endpoints["draft"])
    raw["picks"] = get(endpoints["picks"])

    draft, dset = raw["draft"], raw["draft"].get("settings", {})
    lset = league.get("settings", {})
    slots = collections.Counter(league["roster_positions"])
    start_slots = {k: v for k, v in slots.items() if k != "BN"}
    n = league["total_rosters"]
    flex_types = ["RB", "WR", "TE"]

    users = {u["user_id"]: u for u in raw["users"]}
    agents = []
    for r in sorted(raw["rosters"], key=lambda x: x["roster_id"]):
        u = users.get(r.get("owner_id"))
        agents.append({
            "roster_id": r["roster_id"],
            "user_id": r.get("owner_id"),
            "username": (u or {}).get("display_name"),
            "team_name": ((u or {}).get("metadata") or {}).get("team_name"),
            "occupied": u is not None,
            "keepers": r.get("keepers"),
        })

    start_ms = draft.get("start_time")
    return {
        "_meta": {
            "description": (
                "Machine-readable capture of the league configuration exactly as "
                "returned by the Sleeper public API. This file is the single source "
                "of truth for all downstream code. Do not hand-edit; regenerate with "
                "scripts/refresh_rules.py."
            ),
            "retrieved_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_endpoints": dict(endpoints),
            "api_auth_required": False,
        },
        "league": {
            "league_id": league["league_id"], "name": league["name"],
            "season": league["season"], "season_type": league.get("season_type"),
            "status": league["status"], "sport": league.get("sport"),
            "total_agents": n, "previous_league_id": league.get("previous_league_id"),
        },
        "current_nfl_state": {
            "season": raw["state"].get("season"), "week": raw["state"].get("week"),
            "season_start_date": raw["state"].get("season_start_date"),
        },
        "roster_constraints": {
            "roster_positions_ordered": league["roster_positions"],
            "total_roster_size": len(league["roster_positions"]),
            "starting_slots": start_slots,
            "starting_lineup_size": sum(start_slots.values()),
            "bench_slots": slots.get("BN", 0),
            "injured_reserve_slots": lset.get("reserve_slots"),
            "flex_accepts_types": flex_types,
            "league_wide_starter_demand": {
                "note": (
                    "Number of lineup slots of each type across all agents. Used to "
                    "derive replacement baselines. FLEX demand is satisfiable by any "
                    "of flex_accepts_types."
                ),
                "dedicated_slots": {k: v * n for k, v in start_slots.items() if k != "FLEX"},
                "flex_slots": start_slots.get("FLEX", 0) * n,
            },
        },
        "draft": {
            "draft_id": draft["draft_id"], "type": draft["type"],
            "status": draft["status"], "rounds": dset.get("rounds"),
            "pick_timer_seconds": dset.get("pick_timer"),
            "cpu_autopick_enabled": bool(dset.get("cpu_autopick")),
            "reversal_round": dset.get("reversal_round"),
            "start_time_epoch_ms": start_ms,
            "start_time_utc": (
                datetime.datetime.fromtimestamp(
                    start_ms / 1000, datetime.timezone.utc).isoformat()
                if start_ms else None),
            "draft_order_assigned": draft.get("draft_order") is not None,
            "draft_order": draft.get("draft_order"),
            "slot_to_roster_id": draft.get("slot_to_roster_id"),
            "total_picks": (dset.get("rounds") or 0) * n,
            "picks_made_so_far": len(raw["picks"]),
        },
        "scoring_weights": dict(sorted(league["scoring_settings"].items())),
        "season_structure": {
            "playoff_week_start": lset.get("playoff_week_start"),
            "playoff_teams": lset.get("playoff_teams"),
            "trade_deadline_week": lset.get("trade_deadline"),
            "waiver_type": lset.get("waiver_type"),
            "max_keepers": lset.get("max_keepers"),
            "taxi_slots": lset.get("taxi_slots"),
        },
        "agents": agents,
        "my_team": {
            "username": "Unranked0283",
            "user_id": "1400880093110239232",
            "team_name": "Git Blame Copilot",
        },
    }


def summarise(doc: dict) -> dict:
    """The fields whose drift actually changes behaviour."""
    return {
        "occupied_seats": sorted(a["roster_id"] for a in doc["agents"] if a["occupied"]),
        "draft_order_assigned": doc["draft"]["draft_order_assigned"],
        "picks_made": doc["draft"]["picks_made_so_far"],
        "scoring_weights": doc["scoring_weights"],
        "starting_slots": doc["roster_constraints"]["starting_slots"],
        "keepers": [a["roster_id"] for a in doc["agents"] if a.get("keepers")],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    args = ap.parse_args()

    cfg = config.load()
    fresh = build(cfg.league_id)
    path = config.RULES_PATH
    current = yaml.safe_load(path.read_text())

    before, after = summarise(current), summarise(fresh)
    drift = {k: (before[k], after[k]) for k in after if before.get(k) != after[k]}

    if not drift:
        print("no drift: the committed rules match the live league")
        return 0

    print("DRIFT DETECTED between the committed rules and the live league:\n")
    for key, (old, new) in drift.items():
        print(f"  {key}:\n    was: {old}\n    now: {new}")

    if args.check:
        print("\n--check given; file not modified")
        return 1

    with open(path, "w") as f:
        f.write("# Generated file - do not hand-edit. "
                "Regenerate with scripts/refresh_rules.py.\n")
        yaml.safe_dump(fresh, f, sort_keys=False, default_flow_style=False, width=100)
    print(f"\nrewrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
