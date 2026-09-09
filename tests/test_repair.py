import copy
import dataclasses
import json
import unittest
from unittest.mock import patch

from ffopt import config, inseason, lineup, repair

NOW = 1_789_000_000_000


def cfg(slots, capacity=5, reserve=0, **settings):
    return config.LeagueConfig({
        "roster_constraints": {
            "roster_positions_ordered": slots, "flex_accepts_types": ["RB", "WR", "TE"],
            "total_roster_size": capacity,
        },
        "in_season": {"reserve": {"slots": reserve}, "raw_settings": settings},
    })


def state(owned, free=(), starters=None):
    # Entries: id, position, forecast, injury status, optional fantasy positions.
    entries = list(owned) + list(free)
    return inseason.WeekState(
        season="2026", week=1, my_roster_id=1, my_player_ids=[p[0] for p in owned],
        my_starters=starters or [], rostered_elsewhere=set(),
        projections={p[0]: p[2] for p in entries if p[2] is not None},
        players={p[0]: {"full_name": p[0], "position": p[1], "team": p[0],
                        "injury_status": p[3], "fantasy_positions": p[4] if len(p) > 4 else [p[1]]}
                 for p in entries},
        kickoffs={p[0]: NOW + 3_600_000 for p in entries}, fetched_at_ms=NOW,
    )


class TestEmergencyRepair(unittest.TestCase):
    def test_every_supported_vacancy_gets_a_real_proposal(self):
        for slot in ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF"]:
            with self.subTest(slot=slot):
                pos = "RB" if slot == "FLEX" else slot
                st = state([("out", pos, 12, "Out")], [("add", pos, 9, None)], ["out"])
                result = repair.recommend(st, cfg([slot], 2))
                self.assertEqual(result["status"], "proposed")
                self.assertEqual(result["add"]["player_id"], "add")
                self.assertIsNone(result["drop"])
                self.assertEqual(result["missing_before"], [slot])
                self.assertEqual(result["missing_after"], [])
                self.assertEqual(result["gain"], 9)
                json.dumps(result, allow_nan=False)

    def test_complete_baseline_returns_none_even_if_saved_slot_empty(self):
        st = state([("bench", "WR", 8, None)], [("add", "WR", 20, None)], ["0"])
        self.assertIsNone(repair.recommend(st, cfg(["WR"])))

    def test_no_gap_does_not_offer_speculative_insurance(self):
        st = state([("q", "WR", 8, "Questionable")], [("add", "WR", 20, None)], ["q"])
        self.assertIsNone(repair.recommend(st, cfg(["WR"])))

    def test_one_move_only_for_multiple_holes(self):
        st = state([], [("rb", "RB", 9, None), ("wr", "WR", 10, None)], ["0", "0"])
        r = repair.recommend(st, cfg(["RB", "WR"]))
        self.assertEqual(r["add"]["player_id"], "wr")
        self.assertEqual(r["missing_after"], ["RB"])
        self.assertIn("Only one", " ".join(r["instructions"]))

    def test_capacity_uses_active_roster_not_reserve(self):
        st = state([("ir", "RB", 20, "IR"), ("wr", "WR", 8, None)],
                   [("qb", "QB", 12, None)], ["0", "wr"])
        st.reserve = {"ir"}
        r = repair.recommend(st, cfg(["QB", "WR"], 2, reserve=1))
        self.assertEqual(r["status"], "proposed")
        self.assertIsNone(r["drop"])

    def test_healthy_reserve_requires_activation_not_purchase(self):
        st = state([("ir", "QB", 20, None)], [("qb", "QB", 12, None)], ["0"])
        st.reserve = {"ir"}
        r = repair.recommend(st, cfg(["QB"], 2, reserve=1))
        self.assertEqual(r["status"], "blocked")
        self.assertIn("activation", r["reason"])

    def test_invalid_flagged_reserve_blocks_even_without_precomputed_warning(self):
        st = state([("ir", "QB", 20, "Questionable")], [("qb", "QB", 12, None)], ["0"])
        st.reserve = {"ir"}
        self.assertEqual(repair.recommend(st, cfg(["QB"], 2, reserve=1))["status"], "blocked")

    def test_explicitly_allowed_out_reserve_is_not_a_blocker_or_drop(self):
        st = state([("ir", "QB", 20, "Out")], [("qb", "QB", 12, None)], ["0"])
        st.reserve = {"ir"}
        rules = cfg(["QB"], 1, reserve=1)
        rules.raw["in_season"]["reserve"]["allows"] = {"reserve_allow_out": True}
        r = repair.recommend(st, rules)
        self.assertEqual(r["status"], "proposed")
        self.assertIsNone(r["drop"])

    def test_full_roster_protects_starters_and_selects_smallest_safe_bench(self):
        st = state([("wr", "WR", 20, None), ("cheap", "RB", 2, None), ("other", "TE", 8, None)],
                   [("qb", "QB", 12, None)], ["0", "wr"])
        r = repair.recommend(st, cfg(["QB", "WR"], 3))
        self.assertEqual(r["drop"]["player_id"], "cheap")
        self.assertEqual(r["gain"], 12)
        self.assertEqual(r["missing_after"], [])

    def test_every_injury_flag_and_missing_forecast_are_protected_drops(self):
        for status in ["Questionable", "Doubtful", "Probable", "Out", "IR", "PUP", "Sus", "COV", "Unknown"]:
            with self.subTest(status=status):
                st = state([("star", "RB", .1, status), ("safe", "WR", 5, None)],
                           [("qb", "QB", 12, None)], ["0"])
                r = repair.recommend(st, cfg(["QB"], 2))
                self.assertEqual(r["drop"]["player_id"], "safe")
        st = state([("unknown", "RB", None, None), ("safe", "WR", 5, None)],
                   [("qb", "QB", 12, None)], ["0"])
        self.assertEqual(repair.recommend(st, cfg(["QB"], 2))["drop"]["player_id"], "safe")

    def test_no_safe_drop_blocks_instead_of_releasing_injured_star(self):
        st = state([("Hall", "RB", .6, "Questionable")], [("qb", "QB", 12, None)], ["0"])
        r = repair.recommend(st, cfg(["QB"], 1))
        self.assertEqual(r["status"], "blocked")
        self.assertIsNone(r["drop"])
        self.assertIn("No safe automatic drop", r["reason"])

    def test_locked_bench_is_not_an_automatic_drop(self):
        st = state([("locked", "RB", 1, None), ("safe", "WR", 5, None)],
                   [("qb", "QB", 12, None)], ["0"])
        st.kickoffs["locked"] = NOW
        r = repair.recommend(st, cfg(["QB"], 2))
        self.assertEqual(r["drop"]["player_id"], "safe")

    def test_bye_depressed_forecast_is_not_a_safe_drop(self):
        st = state([("bye-star", "RB", 0, None), ("safe", "WR", 5, None)],
                   [("qb", "QB", 12, None)], ["0"])
        del st.kickoffs["bye-star"]
        result = repair.recommend(st, cfg(["QB"], 2))
        self.assertEqual(result["drop"]["player_id"], "safe")

    def test_full_roster_forecast_gap_does_not_ask_for_a_manual_drop(self):
        st = state([("unknown", "QB", None, None)], [("qb", "QB", 12, None)], ["unknown"])
        result = repair.recommend(st, cfg(["QB"], 1))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("lack forecasts", result["reason"])
        self.assertIn("missing projections", result["instructions"][0])

    def test_deadline_includes_the_drop_not_only_the_later_add(self):
        st = state([("wr", "WR", 10, None), ("cheap", "RB", 2, None)],
                   [("qb", "QB", 12, None)], ["0", "wr"])
        st.kickoffs["cheap"] = NOW + 60_000
        result = repair.recommend(st, cfg(["QB", "WR"], 2))
        self.assertEqual(result["deadline_ms"], NOW + 60_000)

    def test_deadline_includes_a_required_flex_reassignment(self):
        st = state([("out", "WR", 12, "Out"), ("flex", "WR", 10, None)],
                   [("rb", "RB", 9, None)], ["out", "flex"])
        st.kickoffs["flex"] = NOW + 60_000
        result = repair.recommend(st, cfg(["WR", "FLEX"], 3))
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["deadline_ms"], NOW + 60_000)

    def test_locked_starter_preserved_and_unknown_contribution_cancels(self):
        st = state([("locked", "WR", None, "Out")], [("qb", "QB", 12, None)], ["0", "locked"])
        st.kickoffs["locked"] = NOW
        r = repair.recommend(st, cfg(["QB", "WR"], 2))
        self.assertEqual(r["gain"], 12)
        self.assertEqual(r["missing_after"], [])

    def test_unknown_healthy_projection_hole_does_not_trigger_purchase(self):
        st = state([("unknown", "QB", None, None)], [("qb", "QB", 12, None)], ["unknown"])
        r = repair.recommend(st, cfg(["QB"], 2))
        self.assertEqual(r["status"], "blocked")
        self.assertIn("lack forecasts", r["reason"])
        self.assertIsNone(r["gain"])

    def test_unrelated_missing_forecast_does_not_block_real_vacancy(self):
        st = state([("unknown", "RB", None, None)], [("qb", "QB", 12, None)], ["0", "unknown"])
        r = repair.recommend(st, cfg(["QB", "RB"], 2))
        self.assertEqual(r["status"], "proposed")
        self.assertEqual(r["missing_after"], ["RB"])

    def test_unknown_bench_same_position_also_blocks_purchase(self):
        st = state([("out", "QB", 20, "Out"), ("unknown", "QB", None, None)],
                   [("qb", "QB", 12, None)], ["out"])
        self.assertEqual(repair.recommend(st, cfg(["QB"], 3))["status"], "blocked")

    def test_zero_and_negative_forecast_repairs_are_not_claimed_positive(self):
        for value in [0, -1]:
            st = state([], [("qb", "QB", value, None)], ["0"])
            self.assertEqual(repair.recommend(st, cfg(["QB"]))["status"], "blocked")

    def test_missing_free_agent_forecast_is_not_zero(self):
        st = state([], [("qb", "QB", None, None)], ["0"])
        self.assertEqual(repair.recommend(st, cfg(["QB"]))["status"], "blocked")

    def test_nonfinite_owned_forecast_is_blocked_and_json_safe(self):
        st = state([("bad", "RB", float("nan"), None)], [("qb", "QB", 12, None)], ["0"])
        r = repair.recommend(st, cfg(["QB"]))
        self.assertEqual(r["status"], "blocked")
        json.dumps(r, allow_nan=False)

    def test_disabled_adds_and_ir_warning_block_repair(self):
        st = state([], [("qb", "QB", 12, None)], ["0"])
        self.assertIn("disabled", repair.recommend(st, cfg(["QB"], disable_adds=1))["reason"])
        st.move_warnings = ["confirm IR eligibility/activate player before adding"]
        self.assertIn("activation", repair.recommend(st, cfg(["QB"]))["reason"])

    def test_overcapacity_cannot_be_repaired_with_one_add_and_drop(self):
        st = state([("one", "RB", 2, None), ("two", "WR", 3, None)],
                   [("qb", "QB", 12, None)], ["0"])
        self.assertIn("exceeds capacity", repair.recommend(st, cfg(["QB"], 1))["reason"])

    def test_other_owner_and_locked_adds_are_not_candidates(self):
        st = state([], [("owned", "QB", 20, None), ("locked", "QB", 19, None), ("safe", "QB", 12, None)], ["0"])
        st.rostered_elsewhere = {"owned"}
        st.kickoffs["locked"] = NOW
        self.assertEqual(repair.recommend(st, cfg(["QB"]))["add"]["player_id"], "safe")

    def test_explicit_now_controls_locks(self):
        st = state([], [("qb", "QB", 12, None)], ["0"])
        self.assertEqual(repair.recommend(st, cfg(["QB"]), now_ms=NOW)["status"], "proposed")
        self.assertEqual(repair.recommend(st, cfg(["QB"]), now_ms=NOW+3_600_000)["status"], "blocked")

    def test_unlocked_multieligible_starter_can_move_without_being_replaced(self):
        st = state([("dual", "WR", 20, None, ["WR", "TE"])], [("wr", "WR", 12, None)], ["dual", "0"])
        r = repair.recommend(st, cfg(["WR", "TE"], 2))
        self.assertEqual(r["status"], "proposed")
        self.assertEqual(r["missing_after"], [])
        self.assertEqual(r["gain"], 12)

    def test_single_flagged_cover_is_kept_over_cheapest_drop(self):
        st = state([("q", "WR", 20, "Questionable"), ("cover", "WR", 1, None), ("spare", "TE", 5, None)],
                   [("qb", "QB", 12, None)], ["0", "q"])
        r = repair.recommend(st, cfg(["QB", "WR"], 3))
        self.assertEqual(r["drop"]["player_id"], "spare")

    def test_joint_cover_is_kept_even_when_either_backup_alone_covers_single(self):
        st = state([("q1", "WR", 20, "Questionable"), ("q2", "WR", 19, "Questionable"),
                    ("b1", "WR", 1, None), ("b2", "WR", 2, None), ("spare", "TE", 5, None)],
                   [("qb", "QB", 12, None)], ["0", "q1", "q2"])
        r = repair.recommend(st, cfg(["QB", "WR", "WR"], 5))
        self.assertEqual(r["drop"]["player_id"], "spare")

    def test_only_available_drop_would_destroy_flagged_cover(self):
        st = state([("q", "WR", 20, "Questionable"), ("cover", "WR", 1, None)],
                   [("qb", "QB", 12, None)], ["0", "q"])
        r = repair.recommend(st, cfg(["QB", "WR"], 2))
        self.assertEqual(r["status"], "blocked")
        self.assertIn("coverage", r["reason"])

    def test_new_emergency_player_is_not_counted_twice_as_injury_cover(self):
        st = state([("q1", "WR", 20, "Questionable"), ("q2", "WR", 19, "Questionable"),
                    ("cover", "WR", 1, None)],
                   [("add", "QB", 12, None, ["QB", "WR"])], ["0", "q1", "q2"])
        r = repair.recommend(st, cfg(["QB", "WR", "WR"], 3))
        self.assertEqual(r["status"], "blocked")
        self.assertIn("coverage", r["reason"])

    def test_required_instructions_and_recent_waiver_are_explicit(self):
        st = state([("bench", "RB", 1, None)], [("qb", "QB", 12, None)], ["0"])
        st.transactions = [{"status": "complete", "drops": {"qb": 2}}]
        text = " ".join(repair.recommend(st, cfg(["QB"], 1))["instructions"])
        for phrase in ["claim clears before the repair deadline", "recently dropped", "never drop first", "Update to confirm", "not proven future value"]:
            self.assertIn(phrase, text)

    def test_repeatable_and_does_not_mutate_state(self):
        st = state([("bench", "RB", 1, None)], [("qb", "QB", 12, None)], ["0"])
        before = copy.deepcopy(st)
        self.assertEqual(repair.recommend(st, cfg(["QB"], 1)), repair.recommend(st, cfg(["QB"], 1)))
        self.assertEqual(st, before)

    def test_candidate_search_is_bounded(self):
        st = state([], [(f"qb{i}", "QB", i, None) for i in range(20)], ["0"])
        with patch.object(inseason, "free_agents", wraps=inseason.free_agents) as searched:
            r = repair.recommend(st, cfg(["QB"]))
        self.assertEqual(r["add"]["player_id"], "qb19")
        self.assertEqual(searched.call_count, 1)
        self.assertEqual(searched.call_args.kwargs["limit"], repair.FREE_AGENT_LIMIT)

    def test_cardinality_witness_matches_existing_solver(self):
        st = state([("dual", "WR", 8, None, ["WR", "TE"]), ("rb", "RB", 4, None),
                    ("wr", "WR", 3, None), ("unknown", "TE", None, None)])
        candidates = inseason.my_candidates(st)
        rules = cfg(["WR", "TE", "FLEX"])
        for absent in [frozenset(), frozenset({"dual"}), frozenset({"dual", "wr"})]:
            for unknown in [False, True]:
                pool = [dataclasses.replace(c, points=1) if unknown and c.points is None else c
                        for c in candidates if c.player_id not in absent]
                expected = len(lineup.optimise_for(pool, rules).starters())
                self.assertEqual(repair._filled(candidates, rules, absent=absent, allow_missing=unknown), expected)
