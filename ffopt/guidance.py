"""Explicit manager decisions layered over the forecast-led lineup baseline."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypedDict


class Decision(TypedDict):
    action: Literal["hold", "change", "check", "repair", "update"]
    title: str
    instruction: str
    reason: str
    basis: Literal["forecast_baseline", "roster_requirement", "conservative_default", "data_guard"]
    blocked: bool


def decisions(
    *, usable: bool, historical: bool, changes: bool, missing: list[str],
    data_warnings: list[str], flagged: bool, checks_due: bool,
    repair_plan: dict | None = None, locked_unavailable: Sequence[str] = (),
) -> dict[str, Decision]:
    if not usable:
        update: Decision = {
            "action": "update",
            "title": "Update advice before deciding",
            "instruction": "Do not make roster changes from this saved report. Choose Update advice.",
            "reason": (
                "This is a historical report, not a current recommendation."
                if historical else "The current team and source data have not been confirmed."
            ),
            "basis": "data_guard", "blocked": True,
        }
        return {"lineup": update.copy(), "roster": update.copy()}
    if missing or data_warnings:
        if missing and not data_warnings and repair_plan and repair_plan["status"] == "proposed":
            add = repair_plan["add"]["name"]
            drop = repair_plan["drop"]["name"] if repair_plan["drop"] else None
            return {
                "lineup": {
                    "action": "repair", "title": "Fill the open slot, then set your lineup",
                    "instruction": "Complete the recommended roster repair below, update advice, then save the refreshed lineup.",
                    "reason": f"The current report leaves {', '.join(missing)} unfilled.",
                    "basis": "roster_requirement", "blocked": False,
                },
                "roster": {
                    "action": "change", "title": f"Add {add}",
                    "instruction": (
                        f"Add {add} and drop {drop} together after checking the claim deadline. Do not drop first."
                        if drop else f"Add {add} after checking the claim deadline. No drop is needed."
                    ),
                    "reason": "This is a one-week emergency lineup repair, not a speculative upgrade or a proven season-value trade.",
                    "basis": "roster_requirement", "blocked": False,
                },
            }
        repair: Decision = {
            "action": "repair",
            "title": "Resolve the lineup or roster issue",
            "instruction": "Follow the required steps below before considering optional pickups.",
            "reason": (
                f"The report cannot fill these starting slots: {', '.join(missing)}."
                if missing else "A roster or data limitation prevents an all-clear recommendation."
            ),
            "basis": "roster_requirement", "blocked": False,
        }
        roster = repair.copy()
        if missing and repair_plan and repair_plan["status"] == "blocked":
            roster.update({
                "title": "Do not make a pickup from this report yet",
                "instruction": repair_plan["instructions"][0],
                "reason": repair_plan["reason"], "basis": "data_guard",
            })
        return {"lineup": repair, "roster": roster}
    lineup: Decision = {
        "action": "change" if changes else "check" if checks_due else "hold",
        "title": (
            "Make the lineup changes below" if changes else
            "Keep your starters; check their status now" if checks_due else
            "Keep this starting lineup"
        ),
        "instruction": (
            "Save the complete suggested lineup in Sleeper, then update advice to confirm it."
            if changes else
            "Keep these starters in. Update advice and check Sleeper now; replace anyone ruled out before kickoff."
            if checks_due else
            "Do not change your starters right now. Return at the next advice check below."
            if flagged else
            "Do not change your starters right now."
        ),
        "reason": (
            "This follows the current projections, eligibility and lineup locks."
            + (" Questionable alone does not justify benching a player." if flagged else "")
        ),
        "basis": "forecast_baseline", "blocked": False,
    }
    roster: Decision = {
        "action": "hold", "title": "Keep your roster",
        "instruction": "Do not add or drop anyone right now.",
        "reason": (
            "Your starting slots can be filled from this roster. We have not established a benefit "
            "from these moves after future player value and acquisition costs."
        ),
        "basis": "conservative_default", "blocked": False,
    }
    if locked_unavailable:
        names = ", ".join(locked_unavailable)
        lineup["reason"] += f" {names} already locked; leave those slots unchanged."
        roster["reason"] = (
            "A pickup cannot repair an already-locked slot. Keep your roster; "
            "the recommendation covers the remaining movable lineup."
        )
    return {"lineup": lineup, "roster": roster}
