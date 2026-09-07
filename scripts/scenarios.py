#!/usr/bin/env python3
"""Optional, explicitly assumption-dependent waiver and trade analysis."""

import argparse
import dataclasses
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import client, config, inseason, lineup, trades, waivers


def trade_scenario(cfg, state, forecast_week, *, candidate_limit=6, opponent=None):
    if not state.week < forecast_week <= 18:
        raise ValueError("trade forecasts must target a future week, not locked/current scores")
    if reason := trades.trade_guard(cfg, state.week):
        return {"reason": reason, "trades": [], "limitation": trades.LIMITATION}
    projections = inseason._projection_map(
        client.weekly_projections(state.season, forecast_week, refresh=True),
        cfg.scoring_weights,
    )
    if not projections:
        raise ValueError("no future-week forecasts available; cannot value trades")
    games = client.scores(state.season, forecast_week, refresh=True)
    kickoffs = inseason._kickoffs(games)
    future = dataclasses.replace(
        state, week=forecast_week, projections=projections, kickoffs=kickoffs,
        my_starters=[], reserve=set(), games=games,
        unavailable_teams=set(), started_teams=set(),
    )
    for game in games:
        meta = game.get("metadata") or {}
        if meta.get("canceled") or game.get("status") in ("canceled", "postponed"):
            future.unavailable_teams |= {meta["home_team"], meta["away_team"]}
    rosters, excluded = {}, {}
    for raw in state.rosters:
        rid = raw["roster_id"]
        if opponent is not None and rid not in (state.my_roster_id, opponent):
            continue
        reserves = set(raw.get("reserve") or []) | set(raw.get("taxi") or [])
        active = sorted(set(raw.get("players") or []) - reserves)
        candidates, problems = [], []
        for pid in active:
            try:
                c = inseason.candidate(future, pid)
            except inseason.StateError as exc:
                problems.append(str(exc))
                continue
            if c.locked or (reason := lineup.ineligibility(c)):
                problems.append(f"{c.name}: {'target game locked' if c.locked else reason}")
            else:
                candidates.append(c)
        if problems:
            excluded[rid] = problems
        else:
            rosters[rid] = candidates
    if state.my_roster_id not in rosters:
        raise ValueError(f"our future roster cannot be valued completely: {excluded.get(state.my_roster_id)}")
    if opponent is not None and opponent not in rosters:
        raise ValueError(f"opponent unavailable/incomplete: {excluded.get(opponent, opponent)}")
    report = trades.find_trades(
        rosters, state.my_roster_id, cfg, week=state.week,
        horizon_label=f"Current snapshot of projected week {forecast_week}; league scoring",
        candidate_limit=candidate_limit,
    )
    return {
        **dataclasses.asdict(report),
        "excluded_rosters": excluded,
        "player_names": {
            c.player_id: c.name for roster in rosters.values() for c in roster
        },
        "execution_warning": (
            "Review days and app eligibility still apply. Future forecasts are uncertain; "
            "do not execute a trade solely for this single-week objective."
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    waiver = sub.add_parser("waiver", help="solve an explicitly supplied scenario, not fitted league odds")
    waiver.add_argument("--model", type=pathlib.Path, required=True)
    waiver.add_argument("--rank", type=int, required=True)
    waiver.add_argument("--reward", type=float, required=True, help="net utility in the model's units")
    trade = sub.add_parser("trades", help="bounded future-week analysis, never automatic offers")
    trade.add_argument("--week", type=int, choices=range(1, 19), required=True)
    trade.add_argument("--opponent", type=int)
    trade.add_argument("--candidate-limit", type=int, default=6, help="pair-search limit; singles remain exhaustive")
    args = ap.parse_args(argv)
    try:
        if args.command == "waiver":
            model = waivers.WaiverModel.from_dict(json.loads(args.model.read_text()))
            result = dataclasses.asdict(waivers.evaluate_waiver(
                model, rank=args.rank, reward=args.reward,
            ))
        else:
            cfg = config.load()
            current = client.nfl_state().get("week")
            if not isinstance(current, int):
                raise ValueError("missing current week")
            state = inseason.load(cfg, current, refresh=True)
            result = trade_scenario(
                cfg, state, args.week, candidate_limit=args.candidate_limit,
                opponent=args.opponent,
            )
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, client.ApiError) as exc:
        print(f"Scenario analysis unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
