"""Append-only decision snapshots and separately appended realized outcomes.

The lineup observed when advice is requested is not necessarily the final lineup.
Neither comparison is a causal estimate of the advisor's skill.
"""

from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import json
import os
import pathlib
import uuid
from typing import Iterable, Iterator

from . import config, inseason, lineup

LOG_PATH = config.REPO_ROOT / "data" / "decisions.jsonl"


def _append(entry: dict, path: pathlib.Path) -> dict:
    payload = json.dumps(entry, sort_keys=True, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(payload + "\n")
        f.flush()
        os.fsync(f.fileno())
    return json.loads(payload)


def record(
    state: inseason.WeekState, best: lineup.Lineup,
    streams: Iterable[inseason.Stream] = (), *,
    path: pathlib.Path | None = None, cfg: config.LeagueConfig | None = None,
) -> dict:
    entry = {
        "kind": "decision", "schema": 2, "decision_id": str(uuid.uuid4()),
        "ts": state.fetched_at_ms, "season": state.season, "week": state.week,
        "snapshot_id": state.snapshot_id,
        "league_id": state.league_id, "roster_id": state.my_roster_id,
        "sources": state.sources, "projected_total": best.total,
        "recommended": [
            {**dataclasses.asdict(s.player), "slot_index": i, "slot": s.name}
            for i, s in enumerate(best.slots) if s.player
        ],
        "observed_starters": list(state.my_starters),
        "roster_snapshot": [
            dataclasses.asdict(c) for c in inseason.my_candidates(state)
        ] if state.players else [],
        "unfilled": best.unfilled,
        "excluded": [
            {"player_id": c.player_id, "name": c.name, "reason": reason}
            for c, reason in best.excluded
        ],
        "streams": [dataclasses.asdict(s) for s in streams],
        "policy": "expected-points-legal-assignment",
        "policy_version": "1",
        "scoring_weights": cfg.scoring_weights if cfg else None,
        "rules_sha256": hashlib.sha256(json.dumps(
            cfg.raw if cfg else {}, sort_keys=True
        ).encode()).hexdigest(),
    }
    return _append(entry, path or LOG_PATH)


def entries(path: pathlib.Path | None = None) -> Iterator[dict]:
    target = path or LOG_PATH
    if not target.exists():
        return
    with target.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def latest(
    season: str, week: int, path: pathlib.Path | None = None, *,
    league_id: str | None = None, roster_id: int | None = None,
) -> dict | None:
    found = None
    for entry in entries(path):
        if (
            entry.get("kind") == "decision" and entry["season"] == season
            and entry["week"] == week
            and (league_id is None or entry["league_id"] == league_id)
            and (roster_id is None or entry["roster_id"] == roster_id)
        ):
            found = entry
    return found


@dataclasses.dataclass(slots=True)
class Outcome:
    week: int
    recommended_points: float
    observed_points: float
    final_points: float | None = None

    @property
    def delta(self) -> float:
        return self.recommended_points - self.observed_points


def score_week(
    entry: dict, realized: dict[str, float], *, final_starters: list[str] | None = None,
) -> Outcome:
    recommended = [p["player_id"] for p in entry["recommended"]]
    observed = entry["observed_starters"]

    def total(ids: list[str]) -> float:
        ids = [p for p in ids if p and p != "0"]
        missing = set(ids) - realized.keys()
        if missing:
            raise ValueError(f"missing realized scores for {sorted(missing)}")
        return round(sum(realized[p] for p in ids), 2)

    return Outcome(
        entry["week"], total(recommended), total(observed),
        total(final_starters) if final_starters is not None else None,
    )


def record_outcome(
    entry: dict, outcome: Outcome, *, recorded_at_ms: int,
    path: pathlib.Path | None = None, official_points: float | None = None,
) -> dict:
    return _append({
        "kind": "outcome", "schema": 2, "decision_id": entry["decision_id"],
        "ts": recorded_at_ms, "season": entry["season"], "week": entry["week"],
        "league_id": entry["league_id"], "roster_id": entry["roster_id"],
        **dataclasses.asdict(outcome),
        "official_matchup_points": official_points,
        "note": "observed and final lineups are distinct; not a causal skill estimate",
    }, path or LOG_PATH)
