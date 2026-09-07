"""Manager-facing read model. Presentation safeguards do not change the policy."""

from __future__ import annotations

import copy

from . import config, inseason, lineup

RECENCY_SECONDS = 30 * 60
AVAILABILITY_CHECK_MINUTES = 90


def rules_signature(cfg: config.LeagueConfig) -> dict:
    return {
        "user": cfg.my_user_id, "season": cfg.season,
        "scoring": cfg.scoring_weights, "slots": cfg.starting_positions,
        "flex_types": cfg.flex_types,
        "capacity": cfg.roster_size, "rules": cfg.in_season.get("raw_settings"),
    }


def context(cfg: config.LeagueConfig, state: inseason.WeekState) -> dict:
    """Retain only small roster-specific facts, not the entire cached player map."""
    players = {}
    for c in inseason.my_candidates(state):
        players[c.player_id] = {
            "player_id": c.player_id, "name": c.name, "position": c.pos,
            "status": c.status, "kickoff_ms": c.kickoff_ms,
            "locked_at_capture": c.locked, "unavailable": lineup.ineligibility(c),
        }
    waves = []
    for wave in inseason.lock_waves(state):
        affected = [
            {"player_id": c.player_id, "name": c.name} for c in wave.players
            if state.team_of(c.player_id) not in state.unavailable_teams
        ]
        if affected:
            waves.append({"at_ms": wave.kickoff_ms, "players": affected})
    return {
        "players": players, "current_starters": list(state.my_starters),
        "slots": cfg.starting_positions, "rules": rules_signature(cfg),
        "waves": waves,
    }


def build(
    *, mode: str, now_ms: int, league: dict, manifest: dict | None,
    latest_attempt: dict | None, evaluation: dict | None, roster_context: dict | None,
    current_cfg: config.LeagueConfig,
) -> dict:
    if mode not in ("current", "historical"):
        raise ValueError("unsupported advice mode")
    ctx = copy.deepcopy(roster_context or {})
    report = evaluation.get("report", {}) if evaluation else {}
    baseline = next((p for p in report.get("policies", []) if p.get("role") == "baseline"), None)
    recommendation = baseline["recommendation"] if baseline else {}
    as_of = manifest.get("finished_at_ms") if manifest else None
    age = now_ms - as_of if as_of is not None else None
    complete = bool(manifest and manifest["status"] == "complete" and baseline and ctx)
    players = ctx.get("players", {})
    current = ctx.get("current_starters", [])
    current_set = set(current) - {"0", ""}
    missing_forecasts = {
        pid for pid, details in players.items() if details.get("unavailable") == "missing projection"
    }
    missing_current_forecasts = missing_forecasts & current_set
    reasons = []
    state = "recent"
    valid_until = as_of + RECENCY_SECONDS * 1000 if complete else None
    waves = ctx.get("waves", [])
    pickup_locks = [
        p["add"]["kickoff_ms"] for p in recommendation.get("pickups", [])
        if p.get("add", {}).get("kickoff_ms") is not None
    ]
    if complete:
        critical_sources = {"players", "projections", "league", "rosters", "matchups", "scores"}
        receipts = [
            o["received_at_ms"] for o in manifest["observations"]
            if o["role"] in critical_sources
        ]
        if receipts:
            valid_until = min(valid_until, min(receipts) + RECENCY_SECONDS * 1000)
        later_locks = [w["at_ms"] for w in waves if w["at_ms"] > as_of]
        if later_locks:
            valid_until = min(valid_until, min(later_locks))
        later_pickup_locks = [t for t in pickup_locks if t > as_of]
        if later_pickup_locks:
            valid_until = min(valid_until, min(later_pickup_locks))
    if mode == "historical":
        state = "historical"
        reasons.append("Historical report. Do not treat this as current advice.")
    elif not complete:
        state = "unavailable"
        reasons.append("No usable advice is available yet. Update advice to get started.")
    elif age < 0:
        state = "unavailable"
        reasons.append("The server clock precedes this report. Check the clock before acting.")
    elif ctx["rules"] != rules_signature(current_cfg):
        state = "rules_changed"
        reasons.append("Team or league settings changed. Update advice before making changes.")
    elif missing_current_forecasts:
        state = "unavailable"
        names = ", ".join(players[pid]["name"] for pid in sorted(missing_current_forecasts))
        reasons.append(
            f"Forecast data is missing for {names}. That is not a reason to bench them. "
            "Update advice or verify the missing data before changing the lineup."
        )
    elif (
        latest_attempt and latest_attempt["id"] != manifest["id"]
        and (latest_attempt["status"] != "complete" or not latest_attempt.get("analysis_ready", True))
    ):
        state = "unavailable"
        reasons.append("The latest update did not finish with usable advice. Previous advice is shown only for reference.")
    elif any(as_of < w["at_ms"] <= now_ms for w in waves):
        state = "locks_changed"
        reasons.append("Lineup locks have changed since this advice. Update before making changes.")
    elif any(as_of < t <= now_ms for t in pickup_locks):
        state = "stale"
        reasons.append("A suggested pickup's game has started. Update advice before using these options.")
    elif now_ms >= valid_until:
        state = "stale"
        reasons.append("Update advice before making changes; this report or its source data is old.")
    usable = state == "recent"
    message = reasons[0] if reasons else "Recently updated. Verify current availability in Sleeper before acting."

    slots = ctx.get("slots", [])
    rows, meaningful_changes = [], []
    counts = {slot: slots.count(slot) for slot in slots}
    seen = {}
    for row in recommendation.get("lineup", []):
        index, slot, pid = row["slot_index"], row["slot"], row["player_id"]
        seen[slot] = seen.get(slot, 0) + 1
        label = f"{slot} {seen[slot]}" if counts.get(slot, 1) > 1 else slot
        old = current[index] if index < len(current) else None
        old = old if old and old != "0" else None
        details = players.get(pid, {})
        change = "empty" if pid is None else "same"
        if pid is not None and pid != old:
            if pid not in current_set:
                change = "start"
            else:
                original = current.index(pid)
                if original < len(slots) and slots[original] != slot:
                    change = "move"
        if change in ("start", "move"):
            meaningful_changes.append(
                f"{'Start' if change == 'start' else 'Move'} {row['name']} "
                f"{'at' if change == 'start' else 'to'} {label}."
            )
        rows.append({
            "slot_index": index, "slot": slot, "label": label,
            "player_id": pid, "name": row.get("name"), "position": details.get("position"),
            "status": details.get("status"), "projected_points": row.get("projected_points"),
            "kickoff_ms": details.get("kickoff_ms"), "locked_at_capture": row.get("locked", False),
            "current_player_id": old, "current_player_name": players.get(old, {}).get("name"),
            "change": change,
        })
    recommended_ids = {r["player_id"] for r in rows if r["player_id"]}
    bench_changes = [
        f"Bench {players[pid]['name']}."
        for pid in sorted(current_set - recommended_ids)
        if pid in players and pid not in missing_forecasts
    ]
    injuries = [
        {
            "player_id": pid, "name": players[pid]["name"], "status": players[pid]["status"],
            "kickoff_ms": players[pid]["kickoff_ms"],
            "check_at_ms": (
                players[pid]["kickoff_ms"] - AVAILABILITY_CHECK_MINUTES * 60_000
                if players[pid]["kickoff_ms"] is not None else None
            ),
            "note": (
                "Return about 90 minutes before the game, choose Update advice, and check "
                "Sleeper's latest player status. Questionable alone is not a reason to bench."
            ),
        }
        for pid in [r["player_id"] for r in rows if r["player_id"]]
        if pid in players and players[pid]["status"] in lineup.FLAGGED
        and not players[pid]["locked_at_capture"]
    ]
    missing = list(recommendation.get("unfilled", []))
    warnings = list(recommendation.get("warnings", []))
    checks_due = mode == "current" and any(
        p["check_at_ms"] is not None and p["check_at_ms"] <= now_ms < p["kickoff_ms"]
        for p in injuries
    )
    actions = []
    if meaningful_changes or bench_changes or missing:
        same_starters = current_set == recommended_ids and not missing
        actions.append({
            "id": "lineup", "kind": "lineup", "priority": "check" if same_starters else "required",
            "title": "Adjust your position slots" if same_starters else "Review your starting lineup",
            "description": (
                "Same starters and projected total. Slot placement can preserve later FLEX options."
                if same_starters else
                "These suggestions follow this week's projections and eligibility rules. "
                "Use the complete lineup below, then save any changes in Sleeper."
            ),
            "instructions": bench_changes + meaningful_changes + (
                [f"We cannot fill {', '.join(missing)} with eligible rostered players; review replacements in Sleeper."]
                if missing else []
            ),
            "player_ids": [r["player_id"] for r in rows if r["change"] in ("start", "move")],
            "blocked": not usable,
        })
    if injuries:
        actions.append({
            "id": "availability", "kind": "availability", "priority": "check",
            "title": "Check player availability now" if checks_due else "Come back before the games to check availability",
            "description": (
                "Return about 90 minutes before each game, choose Update advice, then verify "
                "the player's latest status in Sleeper. Save any replacements before kickoff."
            ),
            "instructions": [f"{p['name']}: {p['status']}." for p in injuries],
            "player_ids": [p["player_id"] for p in injuries], "blocked": not usable,
        })
    injury_warnings = {
        f"{p['name']}: {p['status']}; verify official inactives" for p in injuries
    }
    data_warnings = [w for w in warnings if w not in injury_warnings]
    for pid in current_set:
        details = players.get(pid, {})
        if details.get("locked_at_capture") and details.get("unavailable"):
            data_warnings.append(
                f"{details['name']}: {details['unavailable']}, but already locked. "
                "That slot cannot be changed; review the remaining open slots."
            )
    for excluded in recommendation.get("excluded", []):
        pid = excluded["player_id"]
        if pid in current_set:
            data_warnings.append(f"{players.get(pid, {}).get('name', pid)}: {excluded['reason']}.")
    if data_warnings:
        actions.append({
            "id": "roster", "kind": "roster", "priority": "required",
            "title": "Review roster or data limitations",
            "description": "Resolve these limitations before relying on the suggested lineup.",
            "instructions": data_warnings, "player_ids": [], "blocked": not usable,
        })
    order = {"roster": 0, "availability": 1, "lineup": 2}
    actions.sort(key=lambda a: (a["priority"] != "required", order[a["kind"]]))
    headline = (
        "Update advice before acting" if not usable else
        "Your lineup needs attention" if missing or data_warnings else
        "Lineup changes are suggested" if meaningful_changes or bench_changes else
        "Check player availability now" if checks_due else
        "No lineup changes suggested right now" if injuries else
        "No lineup changes suggested"
    )
    deadline_clock = as_of if mode == "historical" and as_of is not None else now_ms
    return {
        "mode": mode, "now_ms": now_ms, "league": league,
        "snapshot": {
            "id": manifest["id"], "status": manifest["status"], "week": manifest.get("week"),
            "season": manifest.get("season"), "as_of_ms": as_of,
        } if manifest else None,
        "latest_attempt": {
            "id": latest_attempt["id"], "status": latest_attempt["status"],
            "finished_at_ms": latest_attempt.get("finished_at_ms"),
            "errors": list(latest_attempt.get("errors", [])),
            "analysis_ready": latest_attempt.get("analysis_ready", False),
        } if latest_attempt else None,
        "freshness": {
            "state": state, "usable": usable, "age_ms": age, "message": message,
            "reasons": reasons, "threshold_seconds": RECENCY_SECONDS,
            "valid_until_ms": valid_until,
        },
        "next_deadline": next((w for w in waves if w["at_ms"] > deadline_clock), None),
        "summary": {
            "headline": headline, "lineup_change_count": len(meaningful_changes),
            "injury_count": len(injuries), "missing_slots": missing,
            "projected_total": (
                None if missing or missing_current_forecasts
                or any(r["player_id"] and r["projected_points"] is None for r in rows)
                else recommendation.get("projected_total")
            ),
        },
        "actions": actions, "lineup": rows, "injuries": injuries,
        "pickups": list(recommendation.get("pickups", [])),
        "warnings": warnings, "sleeper_url": "https://sleeper.com/",
        "evaluation_id": evaluation["id"] if evaluation else None,
        "limitations": [
            "Advice only. Make and save roster changes in Sleeper, then update advice to check them.",
            "Projections are estimates, not guaranteed points or a proven advantage.",
            "Recent retrieval does not prove provider news is current. Verify official inactives.",
        ],
    }
