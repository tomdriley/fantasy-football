"""Printable draft sheet generator.

The final fallback. Every software layer shares one point of failure -- the
machine -- so the last line of defence is paper. It must be usable by someone
with no football knowledge, with no computer, inside a 60 second timer.

That constraint drives the format: **tiers and rules, not a ranked list of 200
names.** A ranked list cannot be searched under time pressure and gives no
signal about when a choice matters. Tier boundaries do both, because inside a
tier the options are near-equivalent and the only question is whether the tier
survives to the next turn.
"""

from __future__ import annotations

import datetime
from typing import Sequence

from . import config, pool, season, shrinkage, valuation

TYPE_NOTE = {
    "QB": "1 starts. Deep pool - the 10th best is nearly as good as the best.",
    "RB": "2 start + flex. Scarce and valuable early.",
    "WR": "2 start + flex. Deepest pool; most flex slots end up here.",
    "TE": "1 starts. Steep drop after the top few.",
    "K": "1 starts. Worth ~4 pts over a free agent ALL SEASON. Take last.",
    "DEF": "1 starts. Worth ~18 pts. Take second to last.",
}


def _tier_rows(
    tiers: Sequence[Sequence[pool.Item]], baselines: valuation.Baselines, limit: int
) -> list[str]:
    rows: list[str] = []
    shown = 0
    for number, group in enumerate(tiers, 1):
        if shown >= limit or not group:
            break
        names = []
        for item in group:
            if shown >= limit:
                break
            vor = valuation.value_over_replacement(item, baselines)
            names.append(f"{item.name} ({item.adp:.0f})" if item.adp else item.name)
            shown += 1
        head = f"  T{number}"
        rows.append(f"{head:<5} {', '.join(names)}")
        if number < len(tiers) and shown < limit:
            rows.append("        " + "-" * 58 + "  <- cliff")
    return rows


def render(
    items: Sequence[pool.Item],
    cfg: config.LeagueConfig,
    *,
    lam: float = 0.7,
    depth: int = 14,
) -> str:
    """Render the full printable sheet."""
    adjusted = shrinkage.shrink(items, lam) if lam else list(items)
    baselines = valuation.compute_baselines(adjusted, cfg)
    tiers = valuation.tiers(adjusted, baselines)
    waivers = season.waiver_baselines(adjusted, cfg)

    out: list[str] = []
    add = out.append
    add("=" * 74)
    add("  GIT BLAME COPILOT - DRAFT SHEET".center(74))
    add(f"  {cfg.raw['league']['name']} | {cfg.num_agents} teams | "
        f"{cfg.rounds} rounds | {cfg.pick_timer_seconds}s per pick".center(74))
    add("=" * 74)
    add("")
    add("HOW TO USE: find the highest tier that still has players left, take any")
    add("of them. Inside a tier the choice barely matters - decide fast. When a")
    add("tier is nearly empty and your next turn is far away, that is the moment")
    add("that actually matters.")
    add("")

    add("-" * 74)
    add("ROSTER YOU MUST FILL (10 starters + 5 bench)")
    add("-" * 74)
    slots = ", ".join(
        f"{count}x {position}" for position, count in cfg.starting_slots.items()
    )
    add(f"  {slots}")
    add("  FLEX accepts RB, WR or TE. Bench players score NOTHING unless started.")
    add("")

    add("-" * 74)
    add("RULES (in priority order)")
    add("-" * 74)
    add("  1. Take the best player from the highest live tier. Ignore position")
    add("     until round 8, EXCEPT never take K or DEF early.")
    add("  2. Kicker: LAST pick. Defense: second to last.")
    add("  3. One quarterback only, around rounds 7-10. The best QB scores most")
    add("     points of anyone but is barely better than the 10th best.")
    add("  4. By round 12 make sure you can field: 1 QB, 2 RB, 2 WR, 1 TE, K, DEF.")
    add("  5. If you will NOT check the app weekly, draft a 2nd TE and 2nd QB as")
    add("     cover. If you WILL add free agents each week, skip them and take")
    add("     more RB/WR. This is worth ~50 pts a season either way.")
    add("")

    add("-" * 74)
    add("YOUR PICK NUMBERS BY SEAT (snake order)")
    add("-" * 74)
    add("  seat |  picks (rounds 1-8)")
    for seat in range(1, cfg.num_agents + 1):
        picks = cfg.pick_numbers(seat)[:8]
        add(f"  {seat:>4} |  " + " ".join(f"{p:>3}" for p in picks))
    add("")
    add("  Seats 1 and 10 have 19-pick gaps: plan two rounds ahead.")
    add("  Seat 10 picks twice in a row - treat it as one choice of two players.")
    add("")

    for position in ("RB", "WR", "TE", "QB", "DEF", "K"):
        groups = tiers.get(position) or []
        if not groups:
            continue
        add("-" * 74)
        add(f"{position}   {TYPE_NOTE.get(position, '')}")
        add(f"      replacement level {baselines.replacement.get(position, 0):.0f} pts"
            f" | free agent ~{waivers.get(position, 0):.0f} pts   (number = market ADP)")
        add("-" * 74)
        limit = depth if position in ("RB", "WR") else max(6, depth // 2)
        for row in _tier_rows(groups, baselines, limit):
            add(row)
        add("")

    add("=" * 74)
    add("IF THE TOOL IS DOWN AND THE CLOCK IS RUNNING: take the highest player")
    add("left in the highest tier above that fills a slot you still need.")
    add(f"generated {datetime.datetime.now():%Y-%m-%d %H:%M} | shrinkage lambda={lam}")
    add("=" * 74)
    return "\n".join(out)
