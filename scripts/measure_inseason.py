#!/usr/bin/env python3
"""Cached, descriptive in-season research; NOT an as-of historical backtest.

    python3 scripts/measure_inseason.py --what data-audit
    python3 scripts/measure_inseason.py --cache-dir PATH --what exp-stacking
    python3 scripts/measure_inseason.py --what exp-playoff-window --json-output result.json

No downloads or production decision code are used. Historical API responses
retrieved today may have been revised after games. Record modification dates
and filesystem mtimes cannot establish what was known before a decision.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import pathlib
import random
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import config, scoring

DEFAULT_SEASONS = (2023, 2024, 2025)
LIMITATION = (
    "DESCRIPTIVE / EXPLORATORY ONLY. Cached historical projections and season "
    "ADP are not verified pre-decision snapshots. No fitted management edge, "
    "causal gain, actual league title odds, or contamination pass is established."
)


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def record_score(rec, weights, *, outcome=False):
    """None means unknown, not zero. ADP/points summaries alone are not forecasts.

    For outcomes only, a recorded game/active appearance with no league scoring
    stats is an observed zero under an explicit participation-row convention.
    An absent/empty stat record never establishes a zero.
    """
    stats = rec.get("stats") or {}
    valid = {k: v for k, v in stats.items() if numeric(v)}
    if any(k in weights and scoring.is_scoring_key(k) for k in valid):
        return scoring.payoff(valid, weights), "scoring_fields"
    if outcome and any(valid.get(k, 0) > 0 for k in ("gp", "gms_active")):
        return 0.0, "participation_zero"
    return None, "no_scoring_fields" if stats else "empty_stats"


def iso_date(value):
    if value is None:
        return None
    try:
        if numeric(value):
            return datetime.fromtimestamp(
                value / 1000 if abs(value) > 1e11 else value, timezone.utc
            ).isoformat()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


@dataclass(frozen=True)
class Record:
    points: float | None
    status: str
    position: str | None
    team: str | None
    nested_team: str | None
    date: str | None
    game_id: str | None
    modified: tuple[str, ...]
    adp: float | None
    games: float | None


@dataclass
class Snapshot:
    rows: dict[str, Record]
    source: dict

    @property
    def points(self):
        return {pid: row.points for pid, row in self.rows.items() if row.points is not None}


class CachedData:
    """Read-only cache adapter, including the older research seasonproj alias."""

    def __init__(self, directories, weights, adp_key="adp_ppr"):
        self.directories = [pathlib.Path(p).resolve() for p in directories]
        self.weights = weights
        self.adp_key = adp_key
        self.loaded = {}

    def get(self, year, week=None, kind="proj"):
        key = (year, week, kind)
        if key in self.loaded:
            return self.loaded[key]
        names = (
            [f"projections_{year}.json", f"seasonproj_{year}.json"]
            if week is None else [f"w{kind}_{year}_{week}.json"]
        )
        path = next(
            (directory / name for directory in self.directories for name in names
             if (directory / name).is_file()), None
        )
        source = {"season": year, "week": week, "kind": kind, "as_of_verified": False}
        rows = {}
        if path is None:
            source.update(path=None, missing_file=True, expected_names=names)
        else:
            content = path.read_bytes()
            records = json.loads(content)
            if not isinstance(records, list):
                raise ValueError(f"{path}: expected an API record list")
            source.update(
                path=str(path), sha256=hashlib.sha256(content).hexdigest(),
                filesystem_mtime_utc=iso_date(path.stat().st_mtime),
                missing_file=False, record_count=len(records),
                retrieval_time="not recorded; filesystem mtime is not retrieval provenance",
                metadata_mismatches=0,
            )
            for rec in records:
                if rec.get("player_id") is None:
                    continue
                pid = str(rec["player_id"])
                if pid in rows:
                    raise ValueError(f"{path}: duplicate player_id {pid}")
                if (rec.get("season") is not None and str(rec["season"]) != str(year)) or (
                    rec.get("week") is not None and str(rec["week"]) != str(week)
                ):
                    source["metadata_mismatches"] += 1
                    continue
                player, stats = rec.get("player") or {}, rec.get("stats") or {}
                points, status = record_score(rec, self.weights, outcome=kind == "stat")
                modified = tuple(
                    stamp for field in ("last_modified", "updated_at")
                    if (stamp := iso_date(rec.get(field))) is not None
                )
                adp = stats.get(self.adp_key)
                rows[pid] = Record(
                    points, status,
                    player.get("position") or (player.get("fantasy_positions") or [None])[0],
                    rec.get("team"), player.get("team"), iso_date(rec.get("date")),
                    str(rec["game_id"]) if rec.get("game_id") is not None else None,
                    modified, adp if numeric(adp) and adp > 0 else None,
                    stats.get("gp") if numeric(stats.get("gp")) else None,
                )
        snapshot = Snapshot(rows, source)
        self.loaded[key] = snapshot
        return snapshot

    def manifest(self):
        return [snapshot.source for _, snapshot in sorted(
            self.loaded.items(), key=lambda item: (item[0][0], item[0][1] or 0, item[0][2])
        )]


def average(values):
    values = list(values)
    return statistics.mean(values) if values else None


def describe(values):
    values = list(values)
    return {
        "n": len(values), "mean": average(values),
        "negative": sum(x < 0 for x in values),
        "zero": sum(x == 0 for x in values),
        "positive": sum(x > 0 for x in values),
    }


def pearson(pairs):
    pairs = list(pairs)
    if len(pairs) < 2:
        return None
    xs, ys = zip(*pairs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cross = sum((x - mx) * (y - my) for x, y in pairs)
    denom = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return cross / denom if denom else None


def ranks(values):
    """Average ranks for ties, independent of input ordering."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        for i in order[start:end]:
            out[i] = (start + end - 1) / 2
        start = end
    return out


def spearman(pairs):
    if not pairs:
        return None
    return pearson(zip(ranks([p for p, _ in pairs]), ranks([r for _, r in pairs])))


def roster_needs(roster, positions, cfg):
    counts = Counter(positions.get(pid) for pid in roster)
    dedicated = cfg.dedicated_slots
    deficits = {typ: max(0, n - counts[typ]) for typ, n in dedicated.items()}
    flex_available = sum(max(0, counts[typ] - dedicated.get(typ, 0)) for typ in cfg.flex_types)
    return deficits, max(0, cfg.flex_slots - flex_available)


def legal_roster(roster, positions, cfg):
    if len(roster) != cfg.roster_size or len(set(roster)) != len(roster):
        return False
    missing, flex = roster_needs(roster, positions, cfg)
    return not any(missing.values()) and flex == 0


def select_lineup(roster, rank_by, positions, cfg):
    """Choose only using supplied ranks; missing projections are not zeros."""
    available = {pid for pid in roster if pid in rank_by}
    chosen = []
    for typ, count in cfg.dedicated_slots.items():
        options = sorted(
            (pid for pid in available if positions.get(pid) == typ),
            key=lambda pid: (-rank_by[pid], pid),
        )
        if len(options) < count:
            return None
        chosen.extend(options[:count])
        available.difference_update(options[:count])
    options = sorted(
        (pid for pid in available if positions.get(pid) in cfg.flex_types),
        key=lambda pid: (-rank_by[pid], pid),
    )
    if len(options) < cfg.flex_slots:
        return None
    return tuple(chosen + options[:cfg.flex_slots])


def score_lineup(lineup, values):
    if lineup is None or any(pid not in values for pid in lineup):
        return None
    return sum(values[pid] for pid in lineup)


def synthetic_league(snapshot, cfg):
    """ADP-priority snake proxy preserving capacity and global slot feasibility.

    This is not a reconstruction of actual drafts, and season ADP is not known
    to be preseason. Unlike positional caps, completion checks reserve enough
    players to fill every team's mandatory dedicated and FLEX slots.
    """
    types = set(cfg.dedicated_slots) | set(cfg.flex_types)
    positions = {pid: row.position for pid, row in snapshot.rows.items() if row.position in types}
    pool = sorted(positions, key=lambda pid: (
        snapshot.rows[pid].adp if snapshot.rows[pid].adp is not None else math.inf, pid
    ))
    teams = [[] for _ in range(cfg.num_agents)]
    remaining = Counter(positions.values())
    taken = set()
    for rnd in range(cfg.roster_size):
        seats = range(cfg.num_agents)
        if cfg.is_snake and rnd % 2:
            seats = reversed(seats)
        for seat in seats:
            for pid in pool:
                if pid in taken:
                    continue
                roster = teams[seat] + [pid]
                needs, flex = roster_needs(roster, positions, cfg)
                if sum(needs.values()) + flex > cfg.roster_size - len(roster):
                    continue
                all_needs, flex_needs = Counter(), 0
                for i, team in enumerate(teams):
                    dedicated, wildcards = roster_needs(roster if i == seat else team, positions, cfg)
                    all_needs.update(dedicated)
                    flex_needs += wildcards
                after = remaining.copy()
                after[positions[pid]] -= 1
                if any(after[typ] < n for typ, n in all_needs.items()):
                    continue
                if sum(after[typ] - all_needs[typ] for typ in cfg.flex_types) < flex_needs:
                    continue
                teams[seat].append(pid)
                taken.add(pid)
                remaining = after
                break
            else:
                raise ValueError("cached season pool cannot fill configured league rosters")
    if not all(legal_roster(team, positions, cfg) for team in teams):
        raise ValueError("synthetic roster feasibility invariant failed")
    return teams, [pid for pid in pool if pid not in taken], positions


@dataclass(frozen=True)
class Swap:
    drop: str | None
    add: str | None
    before: tuple[str, ...]
    after: tuple[str, ...]
    projected_delta: float


def choose_single_swap(roster, candidate, projection, positions, cfg):
    """One legal fixed-roster add/drop, or hold; outcomes are deliberately absent."""
    if not legal_roster(roster, positions, cfg):
        raise ValueError("baseline roster must meet capacity and mandatory slots")
    before = select_lineup(roster, projection, positions, cfg)
    if before is None:
        return None
    best = Swap(None, None, before, before, 0.0)
    if candidate is None or candidate in roster or candidate not in projection:
        return best
    baseline = score_lineup(before, projection)
    for drop in sorted(roster):
        changed = [pid for pid in roster if pid != drop] + [candidate]
        if not legal_roster(changed, positions, cfg):
            continue
        after = select_lineup(changed, projection, positions, cfg)
        projected = score_lineup(after, projection)
        if projected is not None and projected - baseline > best.projected_delta:
            best = Swap(drop, candidate, before, after, projected - baseline)
    return best


def realized_swap_delta(swap, outcomes):
    if swap is None:
        return None
    # Shared starters cancel exactly, even if their stat rows are missing.
    removed, added = set(swap.before) - set(swap.after), set(swap.after) - set(swap.before)
    if any(pid not in outcomes for pid in removed | added):
        return None
    return sum(outcomes[pid] for pid in sorted(added)) - sum(outcomes[pid] for pid in sorted(removed))


def measure_lineup(data, seasons, cfg, args):
    rows = []
    for year in seasons:
        season = data.get(year)
        if season.source["missing_file"]:
            rows.append({"season": year, "unavailable": "missing season snapshot"})
            continue
        teams, _, positions = synthetic_league(season, cfg)
        deltas, counts = [], Counter()
        static = [select_lineup(roster, season.points, positions, cfg) for roster in teams]
        for week in range(1, cfg.regular_season_weeks + 1):
            projection, outcomes = data.get(year, week).points, data.get(year, week, "stat").points
            for roster, baseline in zip(teams, static):
                counts["roster_weeks"] += 1
                weekly = select_lineup(roster, projection, positions, cfg)
                if baseline is None or weekly is None:
                    counts["incomplete_projection_lineup"] += 1
                    continue
                delta = realized_swap_delta(Swap(None, None, baseline, weekly, 0), outcomes)
                if delta is None:
                    counts["missing_changed_player_outcomes"] += 1
                else:
                    deltas.append(delta)
        rows.append({"season": year, **counts, "weekly_minus_season_snapshot": describe(deltas)})
    return {
        "scope": "Fixed synthetic rosters; weekly versus season-snapshot chosen lineups. "
                 "Season snapshot is NOT verified preseason. Missing ranks cannot fill a slot. "
                 "Repeated weeks/teams are dependent, not independent trials.",
        "rows": rows,
    }


def measure_streaming(data, seasons, cfg, args):
    rows = []
    for year in seasons:
        season = data.get(year)
        if season.source["missing_file"]:
            rows.append({"season": year, "unavailable": "missing season snapshot"})
            continue
        teams, free, positions = synthetic_league(season, cfg)
        for typ in sorted(set(cfg.dedicated_slots) | set(cfg.flex_types)):
            deltas, action_deltas, forecasts, counts = [], [], [], Counter()
            for week in range(1, cfg.regular_season_weeks + 1):
                projection = data.get(year, week).points
                outcomes = data.get(year, week, "stat").points
                available = sorted(
                    (pid for pid in free if positions[pid] == typ and pid in projection),
                    key=lambda pid: (-projection[pid], pid),
                )
                for roster in teams:
                    counts["roster_weeks"] += 1
                    if len(available) < args.availability_rank:
                        counts["candidate_unavailable"] += 1
                        continue
                    swap = choose_single_swap(
                        roster, available[args.availability_rank - 1], projection, positions, cfg
                    )
                    if swap is None:
                        counts["incomplete_projection_lineup"] += 1
                        continue
                    counts["actions" if swap.add else "holds"] += 1
                    forecasts.append(swap.projected_delta)
                    delta = realized_swap_delta(swap, outcomes)
                    if delta is None:
                        counts["missing_changed_player_outcomes"] += 1
                        continue
                    deltas.append(delta)
                    if swap.add:
                        action_deltas.append(delta)
            rows.append({
                "season": year, "position": typ, **counts,
                "signed_delta_including_holds": describe(deltas),
                "signed_delta_actions_only": describe(action_deltas),
                "projected_delta_including_holds": describe(forecasts),
            })
    return {
        "scope": "SINGLE-SWAP FIXED-ROSTER PROBE, not full-season streaming. "
                 "Each cell independently resets to the same exclusive initial ownership. "
                 "The add is free initially and requires a legal capacity-preserving drop. "
                 "Actions and both lineups use projections only; realized losses are retained. "
                 "Do NOT sum cells as a persistent/competitive waiver policy. "
                 "No priority, acquisition cost, locking, injury eligibility, or future option value model.",
        "availability_rank": args.availability_rank,
        "availability_note": "Nth projected initial free agent is a sensitivity assumption, not modeled competition.",
        "rows": rows,
    }


def forecast_cohort(snapshot, cfg, minimum):
    types = set(cfg.dedicated_slots) | set(cfg.flex_types)
    return {pid: row.points for pid, row in snapshot.rows.items()
            if row.points is not None and row.points > minimum and row.position in types}


def measure_forecast_quality(data, seasons, cfg, args):
    rows = []
    for year in seasons:
        metrics = {alpha: {"pairs": [], "zero": [], "weekly_mae": [], "counts": Counter({
            "forecast_selected": 0, "recency_available": 0, "missing_outcomes": 0,
            "observed_zero_outcomes": 0,
        })}
                   for alpha in (0.0, 0.1, 0.2, 0.3, 0.5)}
        history = {}
        for week in range(1, cfg.regular_season_weeks + 1):
            cohort = forecast_cohort(data.get(year, week), cfg, args.min_projection)
            outcomes = data.get(year, week, "stat").points
            for alpha, metric in metrics.items():
                errors = []
                for pid, forecast in cohort.items():
                    past = [history[w][pid] for w in range(max(1, week - 3), week)
                            if pid in history.get(w, {})]
                    pred = ((1 - alpha) * forecast + alpha * statistics.mean(past)
                            if len(past) >= 2 else forecast)
                    metric["counts"]["forecast_selected"] += 1
                    metric["counts"]["recency_available"] += len(past) >= 2
                    metric["zero"].append(abs(pred - outcomes.get(pid, 0)))
                    if pid not in outcomes:
                        metric["counts"]["missing_outcomes"] += 1
                        continue
                    metric["counts"]["observed_zero_outcomes"] += outcomes[pid] == 0
                    metric["pairs"].append((pred, outcomes[pid]))
                    errors.append(abs(pred - outcomes[pid]))
                if errors:
                    metric["weekly_mae"].append(statistics.mean(errors))
            history[week] = outcomes
        for alpha, metric in metrics.items():
            pairs = metric["pairs"]
            rows.append({
                "season": year, "alpha": alpha, **metric["counts"],
                "observed_outcomes": len(pairs),
                "observed_only_mae": average(abs(p - y) for p, y in pairs),
                "weekly_macro_observed_mae": average(metric["weekly_mae"]),
                "pooled_observed_spearman": spearman(pairs),
                "SENSITIVITY_missing_as_zero_mae": average(metric["zero"]),
            })
    return {
        "scope": "Cohort selected by vendor projection only (strictly above min_projection). "
                 "Absent/empty outcomes remain unknown; primary errors describe observed rows only. "
                 "The separately labeled zero-imputation sensitivity is NOT observed performance. "
                 "Recency uses at least two observed outcomes in the prior three calendar weeks. "
                 "Average ranks handle ties. Alphas are exploratory, not selected on held-out data; "
                 "a recency blend cannot rule out other forecast improvements.",
        "min_projection": args.min_projection, "rows": rows,
    }


def snapshot_audit(snapshot, cfg):
    types = set(cfg.dedicated_slots) | set(cfg.flex_types)
    rows = [row for row in snapshot.rows.values() if row.position in types]
    modified = [stamp for row in rows for stamp in row.modified]
    dates = [row.date for row in rows if row.date]
    return {
        "source": snapshot.source, "recognized_position_records": len(rows),
        "score_present": sum(row.points is not None for row in rows),
        "score_explicit_or_participation_zero": sum(row.points == 0 for row in rows),
        "score_unknown": sum(row.points is None for row in rows),
        "score_status": dict(Counter(row.status for row in rows)),
        "event_date_range": [min(dates), max(dates)] if dates else None,
        "record_modified_range": [min(modified), max(modified)] if modified else None,
        "records_without_event_date": sum(not row.date for row in rows),
        "records_without_modified_timestamp": sum(not row.modified for row in rows),
        "modified_after_event_calendar_date": sum(
            bool(row.date and any(stamp[:10] > row.date[:10] for stamp in row.modified))
            for row in rows
        ),
        "top_level_team_differs_from_current_nested_team": sum(
            bool(row.team and row.nested_team and row.team != row.nested_team) for row in rows
        ),
        "pre_decision_capture_verified": False,
    }


def measure_data_audit(data, seasons, cfg, args):
    snapshots, coverage = [], []
    for year in seasons:
        snapshots.append(snapshot_audit(data.get(year), cfg))
        for week in range(1, 18):
            projection, outcomes = data.get(year, week), data.get(year, week, "stat")
            snapshots.extend([snapshot_audit(projection, cfg), snapshot_audit(outcomes, cfg)])
            cohort = forecast_cohort(projection, cfg, args.min_projection)
            real = outcomes.points
            coverage.append({
                "season": year, "week": week, "forecast_selected": len(cohort),
                "observed_outcomes": sum(pid in real for pid in cohort),
                "no_stat_outcome": sum(pid not in real for pid in cohort),
                "absent_outcome_row": sum(pid not in outcomes.rows for pid in cohort),
                "present_but_unscored_outcome": sum(
                    pid in outcomes.rows and pid not in real for pid in cohort
                ),
                "observed_zero_outcomes": sum(pid in real and real[pid] == 0 for pid in cohort),
            })
    return {
        "scope": "E0 DATA AUDIT. Present-day cache coverage, NOT a point-in-time availability test.",
        "as_of_status": "UNVERIFIED; no archived decision-time captures or kickoff cutoffs supplied.",
        "timestamp_note": "Modification after the event calendar day is an explicit warning. "
                          "Same-day/earlier values do not prove pre-kickoff capture. Cache mtime "
                          "is not a retrieval date. Season ADP/forecasts may be end-of-season revisions. "
                          "Positions use nested player metadata, which also lacks as-of provenance.",
        "outcome_note": "A scoring-field zero or explicit gp/gms_active participation row is zero; "
                        "absent rows, empty stats and metadata-only nonparticipation rows are unknown.",
        "snapshots": snapshots, "weekly_coverage": coverage,
    }


def residual_pairs(data, year, cfg, minimum):
    pairs = {"WR": [], "TE": []}
    controls = {"WR": [], "TE": []}
    counts = Counter()
    for week in range(1, cfg.regular_season_weeks + 1):
        snapshot = data.get(year, week)
        cohort = forecast_cohort(snapshot, cfg, minimum)
        outcomes = data.get(year, week, "stat").points
        qbs = {}
        for pid in sorted(cohort, key=lambda p: (-cohort[p], p)):
            row = snapshot.rows[pid]
            if row.position == "QB" and row.team and row.game_id and row.date:
                qbs.setdefault((row.team, row.game_id, row.date), pid)
        for pid in sorted(cohort):
            row = snapshot.rows[pid]
            if row.position not in pairs:
                continue
            counts["projected_receivers"] += 1
            # Never substitute player.team: it is often the CURRENT team.
            if not row.team or not row.game_id or not row.date:
                counts["missing_historical_team_or_game"] += 1
                continue
            qb = qbs.get((row.team, row.game_id, row.date))
            if qb is None:
                counts["missing_projected_team_qb"] += 1
                continue
            counts["projection_selected_pairs"] += 1
            if pid not in outcomes or qb not in outcomes:
                counts["missing_pair_outcomes"] += 1
                continue
            pairs[row.position].append((outcomes[qb] - cohort[qb], outcomes[pid] - cohort[pid]))
            # A deterministic different-game control, not independent sampling.
            other = next((candidate for key, candidate in sorted(qbs.items())
                          if key[0] != row.team and key[1] != row.game_id), None)
            if other is not None and other in outcomes:
                controls[row.position].append(
                    (outcomes[other] - cohort[other], outcomes[pid] - cohort[pid])
                )
    return pairs, controls, dict(counts)


def shrink_correlation(pairs, prior_pairs):
    corr = pearson(pairs)
    return corr * len(pairs) / (len(pairs) + prior_pairs) if corr is not None else 0.0


def gaussian_tail(mean, variance, target):
    if variance <= 0:
        return float(mean > target) + 0.5 * (mean == target)
    return 0.5 * math.erfc((target - mean) / math.sqrt(2 * variance))


def objective_probe(data, year, cfg, args, training):
    """Synthetic QB+receiver threshold contest; NOT O2 matchup validation."""
    models = {}
    for typ, pairs in training.items():
        if len(pairs) >= 3:
            xs, ys = zip(*pairs)
            vx, vy = statistics.variance(xs), statistics.variance(ys)
            models[typ] = (vx, vy, shrink_correlation(pairs, args.shrink_pairs))
    rows = []
    for margin in (-10.0, 0.0, 10.0):
        counts, point_deltas, hit_deltas, modeled = Counter(), [], [], []
        for week in range(1, cfg.regular_season_weeks + 1):
            snapshot = data.get(year, week)
            cohort = forecast_cohort(snapshot, cfg, args.min_projection)
            outcomes = data.get(year, week, "stat").points
            candidates = sorted(
                (pid for pid in cohort if snapshot.rows[pid].team and snapshot.rows[pid].game_id
                 and snapshot.rows[pid].date), key=lambda p: (-cohort[p], p),
            )
            qbs = [pid for pid in candidates if snapshot.rows[pid].position == "QB"][:cfg.num_agents]
            receiver_slots = cfg.dedicated_slots.get("WR", 0) + cfg.dedicated_slots.get("TE", 0) + cfg.flex_slots
            receivers = [pid for pid in candidates if snapshot.rows[pid].position in models][
                :cfg.num_agents * receiver_slots
            ]
            options = []
            for qb in qbs:
                for receiver in receivers:
                    qr, rr = snapshot.rows[qb], snapshot.rows[receiver]
                    vx, vy, rho = models[rr.position]
                    same_game_team = (qr.team, qr.game_id, qr.date) == (rr.team, rr.game_id, rr.date)
                    covariance = rho * math.sqrt(vx * vy) if same_game_team else 0.0
                    options.append((qb, receiver, cohort[qb] + cohort[receiver], vx + vy + 2 * covariance))
            if not options:
                counts["unavailable_weeks"] += 1
                continue
            o1 = min(options, key=lambda row: (-row[2], row[0], row[1]))
            target = o1[2] + margin
            o2 = min(options, key=lambda row: (
                -gaussian_tail(row[2], row[3], target), -row[2], row[0], row[1]
            ))
            counts["projection_selected_contests"] += 1
            counts["changed_pair"] += o1[:2] != o2[:2]
            modeled.append(gaussian_tail(o2[2], o2[3], target) - gaussian_tail(o1[2], o1[3], target))
            if any(pid not in outcomes for pid in set(o1[:2]) | set(o2[:2])):
                counts["missing_selected_outcomes"] += 1
                continue
            first, second = sum(outcomes[p] for p in o1[:2]), sum(outcomes[p] for p in o2[:2])
            point_deltas.append(second - first)
            hit_deltas.append(
                (float(second > target) + 0.5 * (second == target))
                - (float(first > target) + 0.5 * (first == target))
            )
        rows.append({
            "heldout_season": year, "target_minus_O1_projected_pair_mean": margin, **counts,
            "O2_minus_O1_points": describe(point_deltas),
            "O2_minus_O1_threshold_hits": describe(hit_deltas),
            "model_probability_difference": average(modeled),
        })
    return rows


def measure_stacking(data, seasons, cfg, args):
    training = {"WR": [], "TE": []}
    rows, objectives, prior_years = [], [], []
    for year in sorted(seasons):
        current, controls, counts = residual_pairs(data, year, cfg, args.min_projection)
        for typ in current:
            rows.append({
                "season": year, "receiver_position": typ, "coverage_all_receivers": counts,
                "observed_pair_rows": len(current[typ]), "residual_correlation": pearson(current[typ]),
                "different_game_control_rows": len(controls[typ]),
                "different_game_control_correlation": pearson(controls[typ]),
                "prior_seasons": list(prior_years), "prior_pair_rows": len(training[typ]),
                "prior_shrunk_correlation": (
                    shrink_correlation(training[typ], args.shrink_pairs) if training[typ] else None
                ),
            })
        if prior_years:
            objectives.extend(objective_probe(data, year, cfg, args, training))
        for typ in training:
            training[typ].extend(current[typ])
        prior_years.append(year)
    return {
        "scope": "Top projected QB per historical top-level team/game plus each projected WR/TE. "
                 "Residual = realized minus cached projected league points. Missing pair outcomes "
                 "are reported, not imputed. Prior-season-only correlation shrinks toward zero; "
                 "the shrinkage strength is a declared sensitivity constant, not tuned. "
                 "Pairs share QBs/games and are dependent; no iid confidence intervals or causal claim.",
        "team_provenance": "Use top-level historical team, game_id and date; never nested current team. "
                           "Even these fields have no verified decision-time capture.",
        "shrink_prior_pair_count": args.shrink_pairs,
        "rows": rows,
        "objective_scope": "EXPLORATORY O1/O2 TWO-PLAYER THRESHOLD PROBE, not actual league matchups. "
                           "O1 maximizes cached mean; O2 maximizes Gaussian threshold probability with "
                           "prior-season residual variances and shrunk same-game QB-receiver covariance. "
                           "Targets are O1 mean -10/0/+10, not historical opponents. Independent variance "
                           "is assumed for other pairings; residual bias/tails are not modeled. "
                           "Selection sees projections and prior seasons only; evaluation sees outcomes. "
                           "Prior-year holdout does NOT repair projection as-of contamination. "
                           "Real rosters, contemporaneous projections, opponents and broader covariance "
                           "are needed to evaluate production O1/O2. A small negative probe cannot retire O2.",
        "objective_probe": objectives,
    }


def window_summary(data, year, weeks, pool):
    per_player = {}
    count, total, games, game_points = 0, 0.0, 0.0, 0.0
    missing_files = []
    for week in weeks:
        snapshot = data.get(year, week)
        if snapshot.source["missing_file"]:
            missing_files.append(week)
        for pid in sorted(pool):
            row = snapshot.rows.get(pid)
            if row is None or row.points is None:
                continue
            count += 1
            total += row.points
            per_player.setdefault(pid, []).append(row.points)
            if row.games is not None and row.games > 0:
                games += row.games
                game_points += row.points
    possible = len(pool) * len(weeks)
    return {
        "weeks": list(weeks), "pool_players": len(pool),
        "pool_player_week_slots": possible, "present_projection_player_weeks": count,
        "unknown_projection_player_weeks": possible - count,
        "coverage_fraction": count / possible if possible else None,
        "missing_files": missing_files, "known_forecast_points_sum": total,
        "points_per_present_projection_player_week": total / count if count else None,
        "known_projected_games": games,
        "points_per_known_projected_game": game_points / games if games else None,
        "players_with_any_forecast": len(per_player),
        "players_with_every_week_forecast": sum(len(v) == len(weeks) for v in per_player.values()),
        "mean_player_known_window_sum": average(sum(v) for v in per_player.values()),
    }, per_player


def measure_playoff_window(data, seasons, cfg, args):
    rows = []
    for year in seasons:
        season = data.get(year)
        if season.source["missing_file"]:
            rows.append({"season": year, "unavailable": "missing season snapshot"})
            continue
        teams, _, positions = synthetic_league(season, cfg)
        owned = {pid for team in teams for pid in team}
        types = set(cfg.dedicated_slots) | set(cfg.flex_types)
        # Pool is selected without weekly projections or realized outcomes.
        for typ in sorted(types):
            pool = {pid for pid in owned if positions[pid] == typ}
            early, ep = window_summary(data, year, range(1, 12), pool)
            late, lp = window_summary(data, year, range(15, 18), pool)
            common = sorted(ep.keys() & lp.keys())
            rows.append({
                "season": year, "position": typ, "early": early, "weeks15_17": late,
                "players_observed_in_both_windows": len(common),
                "common_player_late_minus_early_points_per_present_week": describe(
                    statistics.mean(lp[pid]) - statistics.mean(ep[pid]) for pid in common
                ),
            })
    return {
        "scope": "Forecast coverage and known per-game/window sums, weeks 15-17 versus 1-11. "
                 "Pool: fixed legal synthetic season-snapshot ADP league rosters, with total size "
                 "and mandatory positions from current config. This avoids counting thousands "
                 "of placeholder-ADP players as expected starters. Missing forecasts are "
                 "unknown, not zeros; player-week coverage includes byes/injuries and is NOT a "
                 "fraction of scheduled NFL games. Per-game denominators use explicit projected gp. "
                 "Different window lengths and surviving-player composition are not value gains.",
        "identification": "NOT IDENTIFIABLE AS A WEEK-11 TRADE BACKTEST. "
                          "Future weekly historical projections are not shown to have existed at week 11. "
                          "Season ADP itself is not verified preseason. An archived week-11 multiweek "
                          "forecast surface, schedule, rosters and trade prices would be required.",
        "configured_playoff_start": cfg.playoff_week_start,
        "requested_research_window": [15, 16, 17], "rows": rows,
    }


def simulate_title(cfg, *, edge=0.0, trials=10000, seed=42, weekly_sd=26.0):
    """Symmetric zero-edge toy model, not estimated team talent or actual odds."""
    n, k, weeks = cfg.num_agents, cfg.playoff_teams, cfg.regular_season_weeks
    if not k or not 1 < k <= n or weeks < 1 or weekly_sd <= 0 or trials < 1:
        raise ValueError("title simulation needs positive weeks/SD/trials and 2..N playoff teams")
    rng = random.Random(seed)
    playoff_counts, title_counts = [0] * n, [0] * n
    for _ in range(trials):
        wins, points = [0] * n, [0.0] * n
        for _ in range(weeks):
            order = list(range(n))
            rng.shuffle(order)
            scores = [rng.gauss(edge if team == 0 else 0, weekly_sd) for team in range(n)]
            points = [a + b for a, b in zip(points, scores)]
            for a, b in zip(order[::2], order[1::2]):
                wins[a if scores[a] > scores[b] else b] += 1
        seeds = sorted(range(n), key=lambda team: (-wins[team], -points[team]))[:k]
        for team in seeds:
            playoff_counts[team] += 1
        ranks_by_team = {team: rank for rank, team in enumerate(seeds)}
        alive = seeds
        while len(alive) > 1:
            bracket_size = 1 << (len(alive) - 1).bit_length()
            byes = bracket_size - len(alive)
            next_round, playing = alive[:byes], alive[byes:]
            for i in range(len(playing) // 2):
                a, b = playing[i], playing[-1 - i]
                sa, sb = rng.gauss(edge if a == 0 else 0, weekly_sd), rng.gauss(edge if b == 0 else 0, weekly_sd)
                next_round.append(a if sa > sb else b)
            alive = sorted(next_round, key=ranks_by_team.get)
        title_counts[alive[0]] += 1
    return {
        "edge": edge, "trials": trials, "team_playoff_probabilities": [x / trials for x in playoff_counts],
        "team_title_probabilities": [x / trials for x in title_counts],
    }


def measure_title_odds(data, seasons, cfg, args):
    rows = [simulate_title(
        cfg, edge=edge, trials=args.title_trials, seed=args.seed, weekly_sd=args.weekly_sd
    ) for edge in (0, 5, 10, 15, 20)]
    baseline = rows[0]["team_title_probabilities"]
    expected = 1 / cfg.num_agents
    se = math.sqrt(expected * (1 - expected) / args.title_trials)
    return {
        "scope": "ILLUSTRATIVE SYMMETRIC SIMULATION, NOT THIS LEAGUE'S ACTUAL TITLE ODDS. "
                 "Equal baseline talent, independent Gaussian scores, random weekly pairings, "
                 "wins/points seeding, high-low reseeded bracket with top-seed byes. "
                 "One-team edges and weekly SD are assumptions, not historical estimates. "
                 "League size, season length and playoff count come from configuration.",
        "seed": args.seed, "assumed_weekly_sd": args.weekly_sd,
        "zero_edge_expected_title_probability_each": expected,
        "zero_edge_max_abs_deviation": max(abs(x - expected) for x in baseline),
        "zero_edge_each_within_five_MC_standard_errors": all(abs(x - expected) < 5 * se for x in baseline),
        "rows": rows,
    }


MEASUREMENTS = {
    "data-audit": measure_data_audit,
    "lineup": measure_lineup,
    "streaming": measure_streaming,
    "forecast": measure_forecast_quality,
    "exp-stacking": measure_stacking,
    "exp-playoff-window": measure_playoff_window,
    "title": measure_title_odds,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--what", choices=sorted(MEASUREMENTS), action="append")
    parser.add_argument("--seasons", type=int, nargs="+", default=list(DEFAULT_SEASONS))
    parser.add_argument("--cache-dir", type=pathlib.Path, action="append",
                        help="Read-only cache directories, in priority order; default data/cache")
    parser.add_argument("--rules", type=pathlib.Path, help="League rules YAML; default current config")
    parser.add_argument("--json-output", type=pathlib.Path)
    parser.add_argument("--availability-rank", type=int, default=6)
    parser.add_argument("--min-projection", type=float, default=1.0)
    parser.add_argument("--adp-key", default="adp_ppr")
    parser.add_argument("--shrink-pairs", type=float, default=100.0)
    parser.add_argument("--title-trials", type=int, default=10000)
    parser.add_argument("--weekly-sd", type=float, default=26.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    if (args.availability_rank < 1 or args.title_trials < 1 or args.shrink_pairs < 0
            or args.weekly_sd <= 0 or not all(math.isfinite(value) for value in
                                           (args.shrink_pairs, args.weekly_sd, args.min_projection))):
        parser.error("invalid measurement parameter")
    cfg = config.load(args.rules)
    data = CachedData(args.cache_dir or [config.REPO_ROOT / "data/cache"], cfg.scoring_weights, args.adp_key)
    seasons = sorted(set(args.seasons))
    report = {
        "warning": LIMITATION,
        "script_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "settings": {
            "seasons": seasons, "league_teams": cfg.num_agents, "starting_slots": cfg.starting_slots,
            "flex_types": cfg.flex_types, "roster_size": cfg.roster_size,
            "scoring_weights": cfg.scoring_weights, "regular_season_weeks": cfg.regular_season_weeks,
            "playoff_teams": cfg.playoff_teams, "adp_key": args.adp_key,
            "availability_rank": args.availability_rank, "min_projection": args.min_projection,
            "shrink_pairs": args.shrink_pairs, "title_trials": args.title_trials,
            "weekly_sd": args.weekly_sd, "seed": args.seed,
        },
        "measurements": {},
    }
    print(LIMITATION)
    for name in args.what or MEASUREMENTS:
        result = MEASUREMENTS[name](data, seasons, cfg, args)
        report["measurements"][name] = result
        print(f"\n=== {name} ===")
        if name == "data-audit":
            print(result["as_of_status"])
            print(result["timestamp_note"])
            print(result["outcome_note"])
            for year in seasons:
                selected = [row for row in result["weekly_coverage"] if row["season"] == year]
                audits = [row for row in result["snapshots"] if row["source"]["season"] == year]
                modification_dates = [stamp for row in audits for stamp in row["record_modified_range"] or []]
                print(json.dumps({"season": year, **{
                    key: sum(row[key] for row in selected)
                    for key in ("forecast_selected", "observed_outcomes", "no_stat_outcome", "observed_zero_outcomes")
                }, "record_modified_range": (
                    [min(modification_dates), max(modification_dates)] if modification_dates else None
                ), "records_modified_after_event_calendar_date": sum(
                    row["modified_after_event_calendar_date"] for row in audits
                ), "missing_files": sum(row["source"]["missing_file"] for row in audits)}, sort_keys=True))
            print("Full date/coverage/provenance audit is included in --json-output.")
        else:
            print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    report["input_manifest"] = data.manifest()
    if args.json_output:
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
        print(f"\nJSON report: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
