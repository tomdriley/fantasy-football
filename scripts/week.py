#!/usr/bin/env python3
"""Read-only weekly briefing, calendar export, and decision/outcome journal."""

import argparse
import datetime
import json
import pathlib
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import archive, client, collector, config, decisionlog, inseason, lineup, reminders

EASTERN = ZoneInfo("America/New_York")
RULE = "=" * 66


def when(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, EASTERN).strftime("%a %d %b %H:%M %Z")


def countdown(ms: int, now_ms: int) -> str:
    seconds = max(0, (ms - now_ms) // 1000)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    return f"{days}d {hours}h" if days else f"{hours}h {rem // 60}m"


def render(
    state: inseason.WeekState, cfg: config.LeagueConfig, *, quiet: bool = False,
) -> list[str]:
    best = lineup.optimise_for(inseason.my_candidates(state), cfg)
    changes = inseason.lineup_changes(state, best)
    streams = [s for s in inseason.stream_recommendations(state, cfg) if s.worth_doing]
    waves = inseason.lock_waves(state)
    upcoming = [w for w in waves if not w.passed]
    out = [
        RULE, f"WEEK {state.week} | {cfg.raw['league']['name']}",
        f"Snapshot assembled {when(state.fetched_at_ms)}", RULE,
    ]
    if state.snapshot_id:
        out.append(f"Evidence snapshot: {state.snapshot_id}")
    own_match = next((m for m in state.matchups if m["roster_id"] == state.my_roster_id), None)
    own_roster = next((r for r in state.rosters if r["roster_id"] == state.my_roster_id), None)
    if own_roster is not None:
        settings = own_roster.get("settings") or {}
        out.append(
            f"Waiver priority: {settings.get('waiver_position', 'unknown')} "
            f"| record: {settings.get('wins', '?')}-{settings.get('losses', '?')}"
        )
    if own_match and own_match.get("matchup_id") is not None:
        opponents = [
            m for m in state.matchups
            if m.get("matchup_id") == own_match["matchup_id"]
            and m["roster_id"] != state.my_roster_id
        ]
        for opponent in opponents:
            their_ids = [p for p in opponent.get("starters") or [] if p and p != "0"]
            missing = set(their_ids) - state.projections.keys()
            projection = (
                "unknown (missing forecasts)" if missing
                else f"{sum(state.projections[p] for p in their_ids):.1f}"
            )
            out.append(
                f"Opponent roster {opponent['roster_id']}: currently set full-week projection "
                f"{projection}; visible lineup can still change before locks."
            )
            out.append(
                f"Observed official points: us {own_match.get('points', 'unknown')} / "
                f"opponent {opponent.get('points', 'unknown')}"
            )
    if upcoming:
        nxt = upcoming[0]
        out += [
            f"NEXT LOCK {when(nxt.kickoff_ms)} (in {countdown(nxt.kickoff_ms, state.fetched_at_ms)})",
            "  " + ", ".join(c.name for c in nxt.players),
        ]
    else:
        out.append("No remaining roster kickoffs this week.")
    warnings = list(state.move_warnings)
    for slot in best.unfilled:
        warnings.append(f"{slot} is unfilled; obtain an eligible player or check missing data")
    for s in best.slots:
        if s.pinned and s.player and (reason := lineup.ineligibility(s.player)):
            warnings.append(f"{s.player.name}: {reason}, but already LOCKED; cannot replace")
    for c, reason in best.excluded:
        if c.player_id in state.my_starters:
            warnings.append(f"{c.name} is currently starting but {reason}")
        elif reason == "missing projection":
            warnings.append(f"{c.name}: missing bench-player forecast; verify before dropping")
        if reason == "missing projection" and c.pos in inseason.STREAM_POSITIONS:
            warnings.append(f"No {c.pos} pickup gain computed while that forecast is unknown")
    for c in best.flagged:
        warnings.append(
            f"{c.name}: {c.status}; check official inactives about 90 minutes before kickoff"
        )
    if warnings:
        out += ["", "CHECK BEFORE ACTING", *[f"  ! {w}" for w in warnings]]
    if changes:
        out += ["", "LINEUP CHANGES (use the complete target lineup below)"]
        for change in changes:
            suffix = f"slot {change.slot_index + 1} " if change.slot_index >= 0 else ""
            out.append(f"  {change.action:<5} {change.player.name} -> {suffix}{change.slot}")
    elif not quiet:
        out += ["", "No changes to the current lineup under these projections."]
    if streams:
        out += [
            "", "HOLD OPTIONAL MOVES -- these comparisons do not approve an add/drop.",
            "PICKUP COMPARISONS -- projected one-week gains, not proven edges",
        ]
        for stream in streams:
            drop = f"compared with {stream.drop.name}" if stream.drop else "compared with an empty active roster slot"
            out += [
                f"  {stream.position}: {stream.add.name}; {drop}; projected gain {stream.gain:+.2f}",
                f"    {stream.acquisition}",
                f"    {stream.warning}",
            ]
        out.append("  Use My week (/api/v1/advice) for freshness-checked decisions and required lineup repairs.")
    elif not quiet:
        out += ["", "No legal positive DEF/K upgrade found in this snapshot."]
    if not quiet or changes:
        out += ["", f"TARGET LINEUP -- full-week projections {best.total:.1f} (not a live score)"]
        for index, slot in enumerate(best.slots):
            c = slot.player
            if c is None:
                out.append(f"  {index + 1:2} {slot.name:<5} EMPTY")
                continue
            points = f"{c.points:.2f}" if c.points is not None else "unknown"
            flags = " LOCKED" if slot.pinned else ""
            out.append(f"  {index + 1:2} {slot.name:<5} {c.name:<26} {points:>7}{flags}")
        out += ["", "LOCK SCHEDULE"]
        for wave in waves:
            out.append(
                f"  {when(wave.kickoff_ms)} [{'passed' if wave.passed else 'open'}] "
                + ", ".join(c.name for c in wave.players)
            )
        out += ["", "DATA RETRIEVAL TIMES (not proof of the provider's update time)"]
        for key, timestamp in sorted(state.sources.items()):
            out.append(f"  {key}: {when(timestamp)}")
    out += [
        "", "Before each lock: run with --refresh, check news, and SAVE in Sleeper.",
        "Unowned does not mean immediately claimable. Check claim priority and countdown.",
        "Advisory only. No transactions submitted and no background alerts installed.", RULE,
    ]
    return out


def evaluate(cfg: config.LeagueConfig, week: int, path: pathlib.Path) -> dict:
    entry = decisionlog.latest(cfg.season, week, path, league_id=cfg.league_id)
    if entry is None:
        raise ValueError("no logged decision for this league/week")
    games = client.scores(cfg.season, week, refresh=True)
    if not games or not all(
        g.get("status") in ("complete", "final") or (g.get("metadata") or {}).get("is_over")
        for g in games
    ):
        raise ValueError("week is not complete; outcome scoring is unavailable")
    matches = client.matchups(cfg.league_id, week, refresh=True)
    match = next((m for m in matches if m["roster_id"] == entry["roster_id"]), None)
    if match is None:
        raise ValueError("final matchup roster was not found")
    realized = dict(match.get("players_points") or {})
    needed = {p["player_id"] for p in entry["recommended"]} | set(entry["observed_starters"])
    needed.discard("0")
    # Dropped players may no longer appear in the final matchup roster.
    if needed - realized.keys():
        weights = entry.get("scoring_weights")
        if not isinstance(weights, dict):
            raise ValueError("legacy decision lacks captured scoring rules; cannot backfill missing player scores")
        stats = inseason._projection_map(
            client.weekly_stats(cfg.season, week, final=True),
            weights,
        )
        realized.update({p: stats[p] for p in needed - realized.keys() if p in stats})
    outcome = decisionlog.score_week(entry, realized, final_starters=match["starters"])
    saved = decisionlog.record_outcome(
        entry, outcome, recorded_at_ms=int(time.time() * 1000), path=path,
        official_points=(
            match["custom_points"] if match.get("custom_points") is not None else match.get("points")
        ),
    )
    return saved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", type=int, choices=range(1, 19))
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="refetch news/projections and league state")
    ap.add_argument("--log", action="store_true", help="retained for compatibility; logging is now default")
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--log-path", type=pathlib.Path, default=decisionlog.LOG_PATH)
    ap.add_argument("--calendar", type=pathlib.Path, help="export an .ics file; import it to enable alarms")
    ap.add_argument("--score-week", type=int, choices=range(1, 19), help="append an outcome for a completed week")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--capture", action="store_true", help="archive complete inputs before advising")
    group.add_argument("--snapshot", help="offline replay of an archived snapshot; not current advice")
    ap.add_argument("--archive", type=pathlib.Path, default=archive.DEFAULT_PATH)
    args = ap.parse_args()
    try:
        if args.score_week is not None and (args.capture or args.snapshot):
            raise ValueError("--score-week cannot be combined with capture/replay")
        if args.snapshot and (args.refresh or args.calendar or args.log):
            raise ValueError("offline replay cannot refresh sources, export live alarms or enter the live journal")
        if args.score_week is not None:
            cfg = config.load()
            print(json.dumps(evaluate(cfg, args.score_week, args.log_path), indent=2))
            return 0
        if args.snapshot:
            cfg, state = collector.replay(archive.Archive(args.archive, create=False), args.snapshot)
            if args.week is not None and args.week != state.week:
                raise ValueError("requested week differs from the archived snapshot")
        elif args.capture:
            store = archive.Archive(args.archive)
            sid = collector.collect(store, config.load(), week=args.week, refresh=args.refresh)
            cfg, state = collector.replay(store, sid)
        else:
            cfg = config.load()
            nfl = client.nfl_state()
            week = args.week if args.week is not None else nfl.get("week")
            if not isinstance(week, int) or not 1 <= week <= 18:
                raise ValueError("platform has no valid current week; do not guess")
            state = inseason.load(cfg, week, refresh=args.refresh)
        best = lineup.optimise_for(inseason.my_candidates(state), cfg)
        streams = inseason.stream_recommendations(state, cfg)
        should_log = not args.no_log and not args.snapshot
        if should_log:
            decisionlog.record(state, best, streams, path=args.log_path, cfg=cfg)
        if args.calendar:
            args.calendar.write_bytes(reminders.calendar(state).encode("utf-8"))
        if args.snapshot:
            print("OFFLINE REPLAY AT THE ARCHIVED TIME -- not current advice.")
        print("\n".join(render(state, cfg, quiet=args.quiet)))
        if should_log:
            print(f"Decision saved: {args.log_path}")
        if args.calendar:
            print(f"Import {args.calendar} into your calendar to enable 90/15-minute reminders.")
        return 0
    except (client.ApiError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(f"Weekly advice unavailable: {exc}. Verify in Sleeper before acting.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
