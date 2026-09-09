"""One staged emergency pickup, not speculative insurance or season-value advice."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from itertools import combinations
import math

from . import config, inseason, lineup

FREE_AGENT_LIMIT = 3


def _filled(candidates, cfg, *, absent=frozenset(), allow_missing=False, retain=frozenset()):
    """Cardinality-only matching: missing forecasts are eligibility witnesses, never zero points."""
    slots = cfg.starting_positions
    pinned = {c.current_slot: c for c in candidates if c.locked and c.started}
    matched = dict(pinned)
    available = [
        c for c in candidates if not c.locked and c.player_id not in absent
        and (lineup.ineligibility(c) is None
             or (allow_missing and lineup.ineligibility(c) == "missing projection"))
    ]

    def place(c, seen):
        positions = set(c.positions or (c.pos,))
        for i, slot in enumerate(slots):
            accepts = bool(positions & set(cfg.flex_types)) if slot == config.FLEX_SLOT else slot in positions
            if i in pinned or i in seen or not accepts:
                continue
            seen.add(i)
            if i not in matched or place(matched[i], seen):
                matched[i] = c
                return True
        return False

    for c in sorted(available, key=lambda c: (c.player_id not in retain, c.player_id)):
        place(c, set())
    return len(matched)


def recommend(
    state: inseason.WeekState, cfg: config.LeagueConfig, *, now_ms: int | None = None,
) -> dict | None:
    """Use a caller-validated fresh snapshot; propose one atomic add/drop, then require an update."""
    missing = []

    def blocked(reason, instruction="Update the roster, rules and projections, then retry the required repair."):
        return {"status": "blocked", "reason": reason, "add": None, "drop": None, "gain": None,
                "deadline_ms": None,
                "missing_before": list(missing), "missing_after": list(missing),
                "instructions": [instruction]}

    try:
        mine = inseason.my_candidates(state, now_ms=now_ms)
        base = lineup.optimise_for(mine, cfg)
    except (inseason.StateError, ValueError) as exc:
        return blocked(f"Cannot establish a legal forecast baseline: {exc}")
    missing = base.unfilled
    if not missing:
        return None
    if (cfg.in_season.get("raw_settings") or {}).get("disable_adds"):
        return blocked("League additions are disabled.", "Resolve the league add restriction before attempting this repair.")
    if state.move_warnings:
        return blocked("Roster/activation restriction: " + "; ".join(state.move_warnings))
    if any(r.get("taxi") for r in state.rosters if r.get("roster_id") == state.my_roster_id):
        return blocked("Taxi activation and capacity must be resolved before staging a pickup.")
    if len(state.reserve) > cfg.reserve_slots:
        return blocked("Reserve exceeds league capacity; resolve reserve eligibility/activation first.")
    allows = (cfg.in_season.get("reserve") or {}).get("allows") or {}
    reserve_flags = {"Out": "reserve_allow_out", "Doubtful": "reserve_allow_doubtful",
                     "NA": "reserve_allow_na", "Sus": "reserve_allow_sus",
                     "COV": "reserve_allow_cov", "DNR": "reserve_allow_dnr"}
    for c in mine:
        if c.player_id in state.reserve and c.status != "IR" and not allows.get(reserve_flags.get(c.status)):
            return blocked(f"Resolve reserve eligibility/activation for {c.name} ({c.status or 'no designation'}) before adding.")
    active_count = len(set(state.my_player_ids) - state.reserve)
    if active_count > cfg.roster_size:
        return blocked("Active roster exceeds capacity; one add/drop cannot repair the overage.")

    selected = {c.player_id for c in base.starters()}
    structural_before = _filled(mine, cfg, allow_missing=True, retain=selected)
    forecast_holes = structural_before > len(base.starters())
    if structural_before == len(cfg.starting_positions):
        return blocked(
            "Healthy owned players lack forecasts; the apparent vacancies do not require a pickup.",
            "Update the missing projections before making any add/drop.",
        )
    drops = [None] if active_count < cfg.roster_size else sorted(
        (c for c in mine if c.player_id not in selected and c.player_id not in state.reserve
         and not c.locked and not c.status and c.kickoff_ms is not None
         and lineup.ineligibility(c) is None),
        key=lambda c: (c.points, c.player_id),
    )
    if not drops:
        return blocked(
            "No safe automatic drop: bench players are protected by lineup use, reserve, locks, injury flags, byes or missing data.",
            "Free a roster place through an explicitly approved manual move, then update to fill the vacancy.",
        )

    flagged = sorted(c.player_id for c in base.flagged if not c.locked)
    branches = [frozenset(ids) for size in (1, 2) for ids in combinations(flagged, size)]
    positions = set(cfg.starting_positions) - {config.FLEX_SLOT}
    if config.FLEX_SLOT in cfg.starting_positions:
        positions.update(cfg.flex_types)
    adds = {}
    for pos in config.SCORING_TYPES:
        if pos in positions:
            adds.update((c.player_id, c) for c in inseason.free_agents(
                state, pos, limit=FREE_AGENT_LIMIT, now_ms=now_ms,
            ) if c.points is not None and math.isfinite(c.points) and c.kickoff_ms is not None)
    if not adds:
        reason = "Missing owned forecasts may explain these holes; restore those forecasts before buying." if forecast_holes else (
            "No projected, eligible, unlocked unowned candidates can repair " + ", ".join(missing) + "."
        )
        return blocked(reason)

    covers = {
        pid: [_filled(mine + [add], cfg, absent=branch, retain=selected | {pid}) for branch in branches]
        for pid, add in adds.items()
    }
    cover_blocked = unknown_blocked = False
    for drop in drops:
        best = None
        for pid, add in sorted(adds.items()):
            roster = [c for c in mine if drop is None or c.player_id != drop.player_id] + [add]
            # The add must improve real eligibility capacity, not just replace an unknown forecast.
            if _filled(roster, cfg, allow_missing=True, retain=selected | {pid}) <= structural_before:
                unknown_blocked |= forecast_holes
                continue
            if drop and any(
                _filled(roster, cfg, absent=branch, retain=selected | {pid}) < before
                for branch, before in zip(branches, covers[pid])
            ):
                cover_blocked = True
                continue
            # Keep the baseline player set, but allow unlocked WR/TE/FLEX reassignment.
            bonus = 1 + 2 * sum(abs(c.points) for c in roster if c.points is not None)
            retained = [
                dataclasses.replace(c, points=c.points + bonus)
                if c.player_id in selected and c.points is not None else c for c in roster
            ]
            proposed = lineup.optimise_for(retained, cfg)
            after = {c.player_id for c in proposed.starters()}
            if len(proposed.unfilled) >= len(missing) or not selected <= after or pid not in after:
                continue
            originals = {c.player_id: c for c in roster}
            new_points = [originals[p].points for p in after - selected]
            if any(p is None for p in new_points):
                continue
            gain = sum(new_points)  # Unchanged, possibly unknown locked production cancels.
            if gain <= 0 or not math.isfinite(gain):
                continue
            key = (-gain, pid)
            if best is None or key < best[0]:
                best = (key, add, gain, proposed)
        if best is not None:
            _, add, gain, proposed = best
            after_slots = {s.player.player_id: i for i, s in enumerate(proposed.slots) if s.player}
            involved_locks = [add.kickoff_ms]
            if drop:
                involved_locks.append(drop.kickoff_ms)
            involved_locks.extend(
                c.kickoff_ms for c in mine if c.started and not c.locked and c.kickoff_ms is not None
                and after_slots.get(c.player_id) != c.current_slot
            )
            deadline_ms = min(involved_locks)
            deadline = datetime.fromtimestamp(deadline_ms / 1000, timezone.utc).strftime("%a %b %d %H:%M UTC")
            instructions = [
                f"Verify {add.name} can be added immediately or that a claim clears before the repair deadline ({deadline}).",
                inseason.acquisition_note(state, add.player_id) + ".",
            ]
            if drop:
                instructions.append(
                    f"Select {drop.name} as the drop in the same transaction: the lowest current-forecast eligible bench sacrifice, not proven future value."
                )
            instructions += [
                "Execute the add and any drop together; never drop first. A proposed claim is not guaranteed to succeed.",
                "Finish the transaction and required lineup moves before the deadline; an earlier starter or dropped player may lock first.",
                "Keep the other baseline starters; use legal unlocked slot moves to start the replacement.",
                "Update to confirm acquisition and recompute the lineup. Only one add/drop is staged; rerun for any remaining gap.",
                "Immediate single/pair flagged-starter coverage is protected; later news, later locks and season value are not modeled.",
            ]
            if add.flagged:
                instructions.append(f"{add.name} is {add.status}; verify official availability before kickoff.")
            return {"status": "proposed", "reason": "One-week emergency repair increases filled slots and the current lineup forecast.",
                    "add": dataclasses.asdict(add), "drop": dataclasses.asdict(drop) if drop else None,
                    "gain": gain, "deadline_ms": deadline_ms,
                    "missing_before": list(missing), "missing_after": list(proposed.unfilled),
                    "instructions": instructions}
    reasons = []
    if unknown_blocked:
        reasons.append("healthy owned players lack forecasts; do not purchase a substitute for missing data")
    if cover_blocked:
        reasons.append("the available drops reduce immediate single/joint coverage of flagged starters")
    if not reasons:
        reasons.append("no tested add/drop fills a real vacancy with positive forecast gain while retaining baseline starters")
    return blocked(
        "; ".join(reasons) + f" (bounded search: top {FREE_AGENT_LIMIT} free agents per eligible position).",
        "Resolve the stated forecast/drop constraint or choose another explicitly verified repair, then update; do not drop first.",
    )
