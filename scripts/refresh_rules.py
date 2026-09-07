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
import os
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import yaml

from ffopt import config

BASE = "https://api.sleeper.app/v1"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ffopt/0.1 (personal fantasy tool)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


#: Sleeper's waiver_type encoding. Only 0 is exercised by this league, but the
#: others are named so a mis-set league is loud rather than silently mishandled:
#: a FAAB league needs a bidding policy, not a priority-queue one.
WAIVER_TYPES = {0: "rolling_priority", 1: "reverse_standings", 2: "faab"}

# Additional status flags; a missing PUP flag is not evidence about PUP eligibility.
RESERVE_FLAGS = (
    "reserve_allow_out", "reserve_allow_doubtful", "reserve_allow_na",
    "reserve_allow_sus", "reserve_allow_cov", "reserve_allow_dnr",
)


def _in_season(lset: dict) -> dict:
    """In-season action rules: what may be done, when, and at what cost.

    Separate from `season_structure` (the calendar) because these describe the
    *action space* rather than the shape of the season. Every value is read from
    the API; nothing here is hand-typed.
    """
    waiver_type = lset.get("waiver_type")
    budget = lset.get("waiver_budget")
    return {
        "raw_settings": {
            key: lset.get(key) for key in (
                "waiver_type", "waiver_budget", "waiver_day_of_week",
                "waiver_clear_days", "daily_waivers", "daily_waivers_hour",
                "daily_waivers_days", "waiver_bid_min", "disable_trades",
                "trade_deadline", "trade_review_days", "pick_trading",
                "reserve_slots", *RESERVE_FLAGS, "disable_adds", "bench_lock",
                "max_subs", "playoff_week_start", "playoff_teams",
                "playoff_type", "playoff_round_type", "playoff_seed_type",
                "league_average_match", "max_keepers", "taxi_slots",
            )
        },
        "note": (
            "Rules governing in-season actions. waiver_type selects the claim "
            "mechanism: under rolling_priority a successful claim costs queue "
            "position, not currency, so waiver_budget is inert."
        ),
        "waivers": {
            "type_code": waiver_type,
            "type": WAIVER_TYPES.get(waiver_type, "unknown"),
            "budget": budget,
            "budget_is_active": waiver_type == 2,
            "day_of_week_code": lset.get("waiver_day_of_week"),
            "clear_days": lset.get("waiver_clear_days"),
            "daily_waivers": bool(lset.get("daily_waivers")),
            "daily_waivers_hour": lset.get("daily_waivers_hour"),
        },
        "trades": {
            "enabled": not lset.get("disable_trades"),
            "deadline_week": lset.get("trade_deadline"),
            "review_days": lset.get("trade_review_days"),
            "draft_pick_trading": bool(lset.get("pick_trading")),
        },
        "reserve": {
            "slots": lset.get("reserve_slots"),
            "allows": {f: bool(lset.get(f)) for f in RESERVE_FLAGS},
            "extra_status_flags_enabled": any(lset.get(f) for f in RESERVE_FLAGS),
        },
        "roster_moves": {
            "adds_disabled": bool(lset.get("disable_adds")),
            "offseason_adds": bool(lset.get("offseason_adds")),
            "max_in_game_subs": lset.get("max_subs"),
            "bench_locked": bool(lset.get("bench_lock")),
        },
        "playoffs": {
            "week_start": lset.get("playoff_week_start"),
            "teams": lset.get("playoff_teams"),
            "type_code": lset.get("playoff_type"),
            "round_type_code": lset.get("playoff_round_type"),
            "seed_type_code": lset.get("playoff_seed_type"),
        },
    }


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
        "in_season": _in_season(lset),
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
        "roster_configuration": doc["roster_constraints"],
        "keepers": [a["roster_id"] for a in doc["agents"] if a.get("keepers")],
        # A commissioner can change these mid-season. Each one silently
        # invalidates a different policy: the claim mechanism, the trade
        # window, or which designations free an IR slot.
        "in_season": doc.get("in_season"),
        "league_status": doc["league"]["status"],
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

    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with open(temporary, "w") as f:
            f.write("# Generated file - do not hand-edit. "
                    "Regenerate with scripts/refresh_rules.py.\n")
            yaml.safe_dump(fresh, f, sort_keys=False, default_flow_style=False, width=100)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f"\nrewrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
