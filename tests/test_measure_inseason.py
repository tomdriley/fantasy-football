"""Offline regression tests for the descriptive research harness."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ffopt.config import LeagueConfig
from scripts import measure_inseason as measure


def rules(teams=2, slots=None, bench=1, playoff_teams=2, weeks=3):
    slots = slots or {"QB": 1, "WR": 1}
    size = sum(slots.values()) + bench
    return LeagueConfig({
        "league": {"total_agents": teams},
        "roster_constraints": {
            "starting_slots": slots, "total_roster_size": size,
            "bench_slots": bench, "flex_accepts_types": ["RB", "WR", "TE"],
        },
        "draft": {"type": "snake", "rounds": size},
        "scoring_weights": {"pass_yd": 0.04, "rec": 1.0, "rec_yd": 0.1, "fgmiss": -1.0},
        "season_structure": {"playoff_week_start": weeks + 1, "playoff_teams": playoff_teams},
    })


def record(points, position="WR", team="OLD", game="game", date="2023-09-10",
           adp=1, games=1, nested_team="NEW"):
    return measure.Record(
        points, "scoring_fields" if points is not None else "empty_stats",
        position, team, nested_team, date, game, (), adp, games,
    )


def snapshot(rows):
    return measure.Snapshot(rows, {"missing_file": False, "as_of_verified": False})


class MemoryData:
    def __init__(self, snapshots):
        self.snapshots = snapshots

    def get(self, year, week=None, kind="proj"):
        return self.snapshots.get(
            (year, week, kind), measure.Snapshot({}, {"missing_file": True, "as_of_verified": False})
        )


def args(**changes):
    return argparse.Namespace(**{
        "availability_rank": 1, "min_projection": 1.0, "shrink_pairs": 100.0,
        "seed": 42, "title_trials": 2000, "weekly_sd": 26.0, **changes,
    })


class ScoreAndCacheTests(unittest.TestCase):
    def test_missing_metadata_and_explicit_zero_are_distinct(self):
        weights = rules().scoring_weights
        for stats in (None, {}, {"adp_ppr": 20}, {"pts_ppr": 0}, {"gp": 1}):
            with self.subTest(stats=stats):
                self.assertIsNone(measure.record_score({"stats": stats}, weights)[0])
        self.assertEqual(measure.record_score({"stats": {"rec": 0}}, weights)[0], 0)
        self.assertIsNone(measure.record_score({"stats": {}}, weights, outcome=True)[0])
        self.assertEqual(
            measure.record_score({"stats": {"gms_active": 1}}, weights, outcome=True),
            (0, "participation_zero"),
        )
        self.assertIsNone(
            measure.record_score({"stats": {"rec": None, "rec_yd": float("nan")}}, weights)[0]
        )

    def test_uses_league_scoring_and_retains_negative_values(self):
        score, _ = measure.record_score({"stats": {"fgmiss": 2, "pts_ppr": 999}}, rules().scoring_weights)
        self.assertEqual(score, -2)
        score, _ = measure.record_score({"stats": {"pass_yd": 100, "rec": 2}}, rules().scoring_weights)
        self.assertEqual(score, 6)

    def test_cache_alias_is_read_only_and_records_provenance(self):
        payload = json.dumps([{
            "player_id": "p", "season": "2023", "stats": {"rec": 2, "adp_ppr": 3},
            "player": {"position": "WR", "team": "NEW"}, "team": "OLD",
            "last_modified": 1704705643372,
        }]).encode()
        with patch.object(Path, "is_file", lambda p: p.name == "seasonproj_2023.json"), \
                patch.object(Path, "read_bytes", return_value=payload), \
                patch.object(Path, "stat", return_value=SimpleNamespace(st_mtime=1788620000)):
            data = measure.CachedData(["test-read-only-cache"], rules().scoring_weights)
            first = data.get(2023)
            self.assertIs(data.get(2023), first)
        self.assertEqual(first.rows["p"].team, "OLD")
        self.assertEqual(first.points["p"], 2)
        self.assertEqual(len(first.source["sha256"]), 64)
        self.assertFalse(first.source["as_of_verified"])
        self.assertIn("not recorded", first.source["retrieval_time"])

    def test_missing_cache_is_unknown_not_an_empty_success(self):
        with patch.object(Path, "is_file", return_value=False):
            data = measure.CachedData(["test-read-only-cache"], rules().scoring_weights)
            self.assertTrue(data.get(2023, 1).source["missing_file"])
            self.assertEqual(data.get(2023, 1).points, {})

    def test_duplicate_records_are_rejected(self):
        payload = json.dumps([{"player_id": "p", "stats": {"rec": 1}}] * 2).encode()
        with patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "read_bytes", return_value=payload), \
                patch.object(Path, "stat", return_value=SimpleNamespace(st_mtime=1788620000)):
            data = measure.CachedData(["test-read-only-cache"], rules().scoring_weights)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                data.get(2023, 1)

    def test_audit_reports_missing_outcomes_and_late_revisions(self):
        late = measure.Record(5, "scoring_fields", "WR", "OLD", "NEW",
                              "2023-09-10", "game", ("2023-09-12T00:00:00",), 1, 1)
        projections = snapshot({"p": late, "zero": record(5), "empty": record(5), "meta": record(None)})
        outcomes = snapshot({"zero": record(0), "empty": record(None)})
        data = MemoryData({(2023, 1, "proj"): projections, (2023, 1, "stat"): outcomes})
        result = measure.measure_data_audit(data, [2023], rules(), args())
        week = result["weekly_coverage"][0]
        self.assertEqual(week["forecast_selected"], 3)
        self.assertEqual(week["no_stat_outcome"], 2)
        self.assertEqual(week["absent_outcome_row"], 1)
        self.assertEqual(week["present_but_unscored_outcome"], 1)
        self.assertEqual(week["observed_zero_outcomes"], 1)
        audit = measure.snapshot_audit(projections, rules())
        self.assertEqual(audit["modified_after_event_calendar_date"], 1)
        self.assertEqual(audit["score_unknown"], 1)
        self.assertFalse(audit["pre_decision_capture_verified"])
        self.assertIn("UNVERIFIED", result["as_of_status"])

    def test_same_day_dates_never_certify_as_of(self):
        row = measure.Record(1, "scoring_fields", "WR", "A", "A",
                             "2023-09-10", "game", ("2023-09-10T23:59:00",), 1, 1)
        audit = measure.snapshot_audit(snapshot({"p": row}), rules())
        self.assertEqual(audit["modified_after_event_calendar_date"], 0)
        self.assertFalse(audit["pre_decision_capture_verified"])


class RosterAndSwapTests(unittest.TestCase):
    def setUp(self):
        self.cfg = rules()
        self.roster = ["q", "w", "bench"]
        self.positions = {"q": "QB", "w": "WR", "bench": "WR", "free": "WR"}
        self.projection = {"q": 10, "w": 5, "bench": 4, "free": 6}

    def test_signed_loss_gain_and_null_after_identical_projection_decision(self):
        swap = measure.choose_single_swap(self.roster, "free", self.projection, self.positions, self.cfg)
        self.assertEqual(swap.add, "free")
        self.assertIsNotNone(swap.drop)
        self.assertEqual(swap.projected_delta, 1)
        for actual_free, expected in ((2, -8), (15, 5), (10, 0)):
            with self.subTest(actual_free=actual_free):
                outcomes = {"w": 10, "free": actual_free}
                self.assertEqual(measure.realized_swap_delta(swap, outcomes), expected)
                self.assertEqual(
                    measure.choose_single_swap(self.roster, "free", self.projection, self.positions, self.cfg),
                    swap,
                )
        self.assertEqual(self.roster, ["q", "w", "bench"])
        changed = [p for p in self.roster if p != swap.drop] + [swap.add]
        self.assertTrue(measure.legal_roster(changed, self.positions, self.cfg))
        self.assertEqual(len(changed), self.cfg.roster_size)

    def test_hold_uses_no_hindsight_and_null_is_exact(self):
        projection = {**self.projection, "free": 5}
        swap = measure.choose_single_swap(self.roster, "free", projection, self.positions, self.cfg)
        self.assertIsNone(swap.add)
        self.assertEqual(measure.realized_swap_delta(swap, {"free": 1000}), 0)

    def test_missing_changed_outcome_is_not_imputed_zero(self):
        swap = measure.choose_single_swap(self.roster, "free", self.projection, self.positions, self.cfg)
        self.assertIsNone(measure.realized_swap_delta(swap, {"w": 10}))
        self.assertEqual(measure.realized_swap_delta(swap, {"w": 10, "free": 0}), -10)

    def test_missing_shared_outcome_cancels_in_signed_delta(self):
        swap = measure.choose_single_swap(self.roster, "free", self.projection, self.positions, self.cfg)
        self.assertEqual(measure.realized_swap_delta(swap, {"w": 2, "free": 1}), -1)

    def test_streaming_report_retains_negative_realized_delta(self):
        data = MemoryData({
            (2023, None, "proj"): snapshot({}),
            (2023, 1, "proj"): snapshot({
                pid: record(value, self.positions[pid]) for pid, value in self.projection.items()
            }),
            (2023, 1, "stat"): snapshot({"w": record(10), "free": record(2)}),
        })
        with patch.object(measure, "synthetic_league", return_value=(
                [self.roster], ["free"], self.positions)):
            result = measure.measure_streaming(data, [2023], rules(weeks=1), args())
        row = next(row for row in result["rows"] if row["position"] == "WR")
        self.assertEqual(row["signed_delta_including_holds"]["mean"], -8)
        self.assertEqual(row["signed_delta_actions_only"]["negative"], 1)
        self.assertIn("SINGLE-SWAP FIXED-ROSTER", result["scope"])

    def test_no_projection_does_not_fill_mandatory_slot(self):
        projection = {key: value for key, value in self.projection.items() if key != "q"}
        self.assertIsNone(measure.choose_single_swap(self.roster, "free", projection, self.positions, self.cfg))
        self.assertIsNone(measure.select_lineup(self.roster, projection, self.positions, self.cfg))

    def test_negative_known_projection_still_fills_mandatory_slot(self):
        projection = {**self.projection, "q": -5}
        lineup = measure.select_lineup(self.roster, projection, self.positions, self.cfg)
        self.assertIn("q", lineup)
        self.assertEqual(measure.score_lineup(lineup, projection), 0)

    def test_lineup_ties_are_deterministic_not_input_order(self):
        projection = {**self.projection, "bench": 5}
        first = measure.select_lineup(self.roster, projection, self.positions, self.cfg)
        second = measure.select_lineup(list(reversed(self.roster)), projection, self.positions, self.cfg)
        self.assertEqual(first, second)

    def test_invalid_roster_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "capacity"):
            measure.choose_single_swap(self.roster + ["free"], None, self.projection, self.positions, self.cfg)

    def test_synthetic_draft_fills_configured_slots_and_exclusive_ownership(self):
        cfg = rules(teams=3, slots={"QB": 1, "WR": 1, "TE": 1, "K": 1, "FLEX": 2}, bench=2)
        rows = {}
        # Favor QBs extremely strongly; simple positional caps can leave K/TE empty.
        for typ, n in (("QB", 30), ("WR", 16), ("TE", 6), ("K", 3)):
            for i in range(n):
                rows[f"{typ}{i:02}"] = record(100 - i, typ, adp=len(rows) + 1)
        teams, free, positions = measure.synthetic_league(snapshot(rows), cfg)
        owned = [pid for team in teams for pid in team]
        self.assertEqual(len(set(owned)), len(owned))
        self.assertFalse(set(owned) & set(free))
        self.assertEqual(set(owned) | set(free), set(rows))
        for team in teams:
            self.assertTrue(measure.legal_roster(team, positions, cfg))
            self.assertEqual(len(measure.select_lineup(team, {p: 1 for p in rows}, positions, cfg)), 6)
        self.assertEqual(measure.synthetic_league(snapshot(rows), cfg)[0], teams)

    def test_draft_infeasible_pool_fails_instead_of_omitting_slots(self):
        cfg = rules(teams=2, slots={"QB": 1, "K": 1}, bench=0)
        rows = {str(i): record(i, "QB", adp=i + 1) for i in range(10)}
        with self.assertRaisesRegex(ValueError, "cannot fill"):
            measure.synthetic_league(snapshot(rows), cfg)


class ForecastAndExperimentTests(unittest.TestCase):
    def test_ties_get_average_ranks_and_constant_is_undefined(self):
        self.assertEqual(measure.ranks([2, 1, 1, 3]), [2, 0.5, 0.5, 3])
        self.assertAlmostEqual(measure.spearman([(1, 10), (1, 10), (2, 20)]), 1)
        self.assertAlmostEqual(measure.spearman([(1, 20), (1, 20), (2, 10)]), -1)
        self.assertIsNone(measure.spearman([(1, 2), (1, 3)]))
        self.assertIsNone(measure.spearman([]))

    def test_forecast_cohort_retains_unobserved_outcomes(self):
        data = MemoryData({
            (2023, 1, "proj"): snapshot({"known": record(10), "missing": record(8), "small": record(0)}),
            (2023, 1, "stat"): snapshot({"known": record(0), "small": record(100)}),
        })
        result = measure.measure_forecast_quality(data, [2023], rules(weeks=1), args())
        for row in result["rows"]:
            self.assertEqual(row["forecast_selected"], 2)
            self.assertEqual(row["observed_outcomes"], 1)
            self.assertEqual(row["missing_outcomes"], 1)
            self.assertEqual(row["observed_zero_outcomes"], 1)
            self.assertEqual(row["observed_only_mae"], 10)
            self.assertEqual(row["SENSITIVITY_missing_as_zero_mae"], 9)

    def test_recency_does_not_reach_across_missing_calendar_weeks(self):
        data = MemoryData({
            (2023, 1, "stat"): snapshot({"p": record(100)}),
            (2023, 2, "stat"): snapshot({"p": record(100)}),
            (2023, 6, "proj"): snapshot({"p": record(10)}),
            (2023, 6, "stat"): snapshot({"p": record(10)}),
        })
        result = measure.measure_forecast_quality(data, [2023], rules(weeks=6), args())
        self.assertTrue(all(row["observed_only_mae"] == 0 for row in result["rows"]))
        self.assertTrue(all(row["recency_available"] == 0 for row in result["rows"]))

    def test_stacking_uses_historical_team_not_current_nested_team(self):
        data = MemoryData({
            (2023, 1, "proj"): snapshot({
                "q": record(10, "QB", team="A", nested_team="CURRENT_Q"),
                "q_low": record(9, "QB", team="A", nested_team="CURRENT_Q"),
                "w": record(5, "WR", team="A", nested_team="CURRENT_W"),
                "wrong": record(5, "WR", team="B", nested_team="CURRENT_Q"),
                "unknown": record(5, "WR", team=None, nested_team="CURRENT_Q"),
            }),
            (2023, 1, "stat"): snapshot({"q": record(11), "q_low": record(500), "w": record(7)}),
        })
        pairs, _, coverage = measure.residual_pairs(data, 2023, rules(weeks=1), 1)
        self.assertEqual(pairs["WR"], [(1, 2)])
        self.assertEqual(coverage["projection_selected_pairs"], 1)
        self.assertEqual(coverage["missing_historical_team_or_game"], 1)
        self.assertEqual(coverage["missing_projected_team_qb"], 1)

    def test_shrinkage_and_covariance_have_objective_effects(self):
        pairs = [(1, 2), (2, 4), (3, 6)]
        self.assertAlmostEqual(measure.shrink_correlation(pairs, 3), 0.5)
        self.assertGreater(measure.gaussian_tail(20, 200, 30), measure.gaussian_tail(20, 100, 30))
        self.assertLess(measure.gaussian_tail(20, 200, 10), measure.gaussian_tail(20, 100, 10))

    def test_stacking_objective_uses_only_prior_seasons(self):
        data = MemoryData({})
        seen = []

        def pairs(_data, year, _cfg, _minimum):
            return {"WR": [(year, 1)] * 3, "TE": []}, {"WR": [], "TE": []}, {}

        def objective(_data, year, _cfg, _args, training):
            seen.append((year, list(training["WR"])))
            return []

        with patch.object(measure, "residual_pairs", side_effect=pairs), \
                patch.object(measure, "objective_probe", side_effect=objective):
            measure.measure_stacking(data, [2025, 2023, 2024], rules(), args())
        self.assertEqual(seen[0], (2024, [(2023, 1)] * 3))
        self.assertEqual(seen[1], (2025, [(2023, 1)] * 3 + [(2024, 1)] * 3))

    def test_playoff_missing_forecasts_are_unknown_not_zero_or_future_knowledge(self):
        data = MemoryData({
            (2023, None, "proj"): snapshot({"p": record(100), "missing": record(100)}),
            (2023, 1, "proj"): snapshot({"p": record(6)}),
            (2023, 15, "proj"): snapshot({"p": record(9)}),
            (2023, 16, "proj"): snapshot({"p": record(0)}),
        })
        result = measure.measure_playoff_window(
            data, [2023], rules(teams=1, slots={"WR": 1}, bench=1), args()
        )
        wr = next(row for row in result["rows"] if row["position"] == "WR")
        late = wr["weeks15_17"]
        self.assertEqual(late["pool_player_week_slots"], 6)
        self.assertEqual(late["present_projection_player_weeks"], 2)
        self.assertEqual(late["unknown_projection_player_weeks"], 4)
        self.assertEqual(late["known_forecast_points_sum"], 9)
        self.assertEqual(late["points_per_known_projected_game"], 4.5)
        self.assertEqual(late["players_with_every_week_forecast"], 0)
        self.assertIn("NOT IDENTIFIABLE", result["identification"])


class TitleSanityTests(unittest.TestCase):
    def test_equal_teams_have_approximately_one_in_ten_title_probability(self):
        cfg = rules(teams=10, playoff_teams=6, weeks=14)
        result = measure.simulate_title(cfg, trials=6000)
        self.assertAlmostEqual(sum(result["team_title_probabilities"]), 1)
        self.assertAlmostEqual(sum(result["team_playoff_probabilities"]), 6)
        for probability in result["team_title_probabilities"]:
            self.assertLess(abs(probability - 0.1), 0.025)

    def test_configured_bracket_sizes_and_seed_are_respected(self):
        cfg = rules(teams=4, playoff_teams=3, weeks=2)
        result = measure.simulate_title(cfg, trials=100, seed=19)
        self.assertEqual(result, measure.simulate_title(cfg, trials=100, seed=19))
        self.assertAlmostEqual(sum(result["team_title_probabilities"]), 1)
        self.assertAlmostEqual(sum(result["team_playoff_probabilities"]), 3)


if __name__ == "__main__":
    unittest.main()
