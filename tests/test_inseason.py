"""Synthetic integration checks for roster legality, locks and missing data."""

import dataclasses
import unittest
from unittest.mock import patch

from ffopt import config, inseason, lineup

HOUR = 3_600_000
SUNDAY_1PM = 1_789_318_800_000
SUNDAY_4PM = SUNDAY_1PM + 3 * HOUR


def player(pid, pos, team, **meta):
    return {
        "player_id": pid, "position": pos, "team": team,
        "full_name": f"{pos} {pid}", "fantasy_positions": [pos], **meta,
    }


def state(**kw):
    defaults = dict(
        season="2026", week=1, my_roster_id=2, my_player_ids=[],
        my_starters=[], rostered_elsewhere=set(), projections={}, players={},
        kickoffs={}, fetched_at_ms=SUNDAY_1PM - HOUR,
    )
    defaults.update(kw)
    return inseason.WeekState(**defaults)


def small_cfg(capacity=2):
    return config.LeagueConfig({
        "scoring_weights": {"rec": 1.0},
        "roster_constraints": {
            "roster_positions_ordered": ["DEF", "K"],
            "starting_slots": {"DEF": 1, "K": 1},
            "flex_accepts_types": [], "total_roster_size": capacity,
        },
    })


def stream_state():
    return state(
        my_player_ids=["mydef", "myk"], my_starters=["mydef", "myk"],
        players={
            "mydef": player("mydef", "DEF", "DET"), "myk": player("myk", "K", "DET"),
            "fadef": player("fadef", "DEF", "CHI"), "fak": player("fak", "K", "CHI"),
        },
        projections={"mydef": 5.0, "myk": 7.0, "fadef": 9.0, "fak": 6.0},
        kickoffs={"DET": SUNDAY_4PM, "CHI": SUNDAY_4PM},
    )


class TestWeeklyState(unittest.TestCase):
    def test_bye_and_lock_are_different(self):
        st = state(
            players={"early": player("early", "RB", "DET"), "late": player("late", "RB", "LAC"),
                     "bye": player("bye", "RB", "GB")},
            kickoffs={"DET": SUNDAY_1PM, "LAC": SUNDAY_4PM},
        )
        self.assertTrue(st.on_bye("bye"))
        self.assertFalse(st.is_locked("bye", now_ms=SUNDAY_4PM))
        self.assertTrue(st.is_locked("early", now_ms=SUNDAY_1PM))
        self.assertFalse(st.is_locked("late", now_ms=SUNDAY_1PM))

    def test_no_team_and_missing_projection_are_not_startable(self):
        st = state(players={"x": player("x", "K", None)}, projections={"x": 9})
        self.assertIn("no current NFL team", lineup.ineligibility(inseason.candidate(st, "x")))
        st.players["x"]["team"] = "DET"
        st.kickoffs["DET"] = SUNDAY_4PM
        st.projections = {}
        self.assertEqual(lineup.ineligibility(inseason.candidate(st, "x")), "missing projection")

    def test_reserve_candidate_excluded(self):
        st = stream_state()
        st.reserve.add("myk")
        self.assertIn("reserve", lineup.ineligibility(inseason.candidate(st, "myk")))

    def test_current_slot_keeps_empty_sentinels(self):
        st = stream_state()
        st.my_starters = ["0", "myk"]
        st.fetched_at_ms = SUNDAY_4PM
        c = inseason.candidate(st, "myk")
        self.assertTrue(c.started)
        self.assertTrue(c.locked)
        self.assertEqual(c.current_slot, 1)

    def test_every_locked_starter_survives_live_path(self):
        st = stream_state()
        st.fetched_at_ms = SUNDAY_4PM
        st.players["mydef"]["injury_status"] = "Out"
        best = lineup.optimise_for(inseason.my_candidates(st), small_cfg())
        self.assertEqual([s.player.player_id for s in best.slots], st.my_starters)
        self.assertTrue(all(s.pinned for s in best.slots))
        self.assertEqual(inseason.lineup_changes(st, best), [])

    def test_unknown_rostered_player_fails_loudly(self):
        st = stream_state()
        st.my_player_ids.append("missing")
        with self.assertRaises(inseason.StateError):
            inseason.my_candidates(st)

    def test_missing_schedule_is_not_all_byes(self):
        with self.assertRaises(inseason.StateError):
            inseason._kickoffs([])
        with self.assertRaises(inseason.StateError):
            inseason._kickoffs([{"metadata": {"home_team": "DET", "away_team": "GB"}}])

    def test_adp_only_is_missing_projection(self):
        self.assertEqual(inseason._projection_map(
            [{"player_id": "x", "stats": {"adp_std": 1}}], {"rec": 1}
        ), {})
        self.assertEqual(inseason._projection_map(
            [{"player_id": "x", "stats": {"rec": 0}}], {"rec": 1}
        ), {"x": 0})

    def test_reserve_and_taxi_are_owned(self):
        self.assertEqual(inseason._roster_players(
            {"players": ["a"], "reserve": ["b"], "taxi": ["c"]}
        ), {"a", "b", "c"})

    def test_cancelled_game_is_not_silently_startable(self):
        st = stream_state()
        st.unavailable_teams.add("CHI")
        self.assertEqual(inseason.free_agents(st, "K"), [])


class TestStreaming(unittest.TestCase):
    def test_gain_uses_full_lineup_after_drop(self):
        recs = inseason.stream_recommendations(stream_state(), small_cfg())
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].drop.player_id, "mydef")
        self.assertEqual(recs[0].gain, 4.0)
        self.assertLess(recs[1].gain, 0)
        self.assertFalse(recs[1].worth_doing)

    def test_bye_incumbent_can_be_replaced_with_paired_drop(self):
        st = stream_state()
        del st.kickoffs["DET"]
        recs = {s.position: s for s in inseason.stream_recommendations(st, small_cfg())}
        self.assertEqual(recs["DEF"].gain, 9)
        self.assertEqual(recs["DEF"].drop.player_id, "mydef")

    def test_locked_starter_never_has_pickup_option(self):
        st = stream_state()
        st.kickoffs["DET"] = SUNDAY_1PM
        self.assertEqual(inseason.stream_recommendations(
            st, small_cfg(), now_ms=SUNDAY_1PM + HOUR
        ), [])

    def test_open_slot_is_a_valid_pickup_without_drop(self):
        st = stream_state()
        st.my_player_ids.remove("myk")
        st.my_starters[1] = "0"
        st.rostered_elsewhere.add("myk")
        recs = {s.position: s for s in inseason.stream_recommendations(st, small_cfg())}
        self.assertTrue(recs["K"].worth_doing)
        self.assertIsNone(recs["K"].drop)

    def test_full_roster_no_kicker_does_not_silently_drop_skill_player(self):
        st = stream_state()
        st.my_player_ids = ["mydef", "rb"]
        st.players["rb"] = player("rb", "RB", "DET")
        st.projections["rb"] = 15
        st.rostered_elsewhere.add("myk")
        st.my_starters[1] = "0"
        self.assertNotIn("K", {s.position for s in inseason.stream_recommendations(st, small_cfg())})

    def test_invalid_ir_warns_and_suppresses_pickups(self):
        st = stream_state()
        st.move_warnings = ["ineligible reserve occupant"]
        self.assertEqual(inseason.stream_recommendations(st, small_cfg()), [])

    def test_recent_drop_is_not_called_immediate_free_agent(self):
        st = stream_state()
        st.transactions = [{"status": "complete", "drops": {"fadef": 3}}]
        rec = inseason.stream_recommendations(st, small_cfg())[0]
        self.assertIn("recently dropped", rec.acquisition)
        self.assertIn("waiver", rec.acquisition)

    def test_other_teams_players_not_offered(self):
        st = stream_state()
        st.rostered_elsewhere = {"fadef", "fak"}
        self.assertEqual(inseason.stream_recommendations(st, small_cfg()), [])

    def test_missing_incumbent_projection_does_not_become_free_gain(self):
        st = stream_state()
        st.projections["mydef"] = 20
        before = inseason.stream_recommendations(st, small_cfg())
        self.assertFalse(any(s.position == "DEF" and s.worth_doing for s in before))
        del st.projections["mydef"]
        self.assertNotIn("DEF", {s.position for s in inseason.stream_recommendations(st, small_cfg())})

    def test_missing_same_position_bench_forecast_also_blocks_gain(self):
        st = stream_state()
        st.my_player_ids.append("unknown")
        st.players["unknown"] = player("unknown", "DEF", "DET")
        recs = inseason.stream_recommendations(st, small_cfg(capacity=3))
        self.assertNotIn("DEF", {s.position for s in recs})


class TestCalendarAndChanges(unittest.TestCase):
    def test_lock_waves_skip_passed_and_unowned_teams(self):
        st = stream_state()
        st.kickoffs = {"DET": SUNDAY_1PM, "CHI": SUNDAY_4PM}
        self.assertEqual(len(inseason.lock_waves(st)), 1)
        self.assertIsNone(inseason.next_lock(st, now_ms=SUNDAY_4PM))

    def test_same_selected_set_but_changed_slot_is_shown(self):
        st = state(
            my_player_ids=["early", "late"], my_starters=["late", "early"],
            players={"early": player("early", "WR", "DET"), "late": player("late", "WR", "CHI")},
            projections={"early": 10, "late": 20},
            kickoffs={"DET": SUNDAY_1PM, "CHI": SUNDAY_4PM},
        )
        best = lineup.optimise(
            inseason.my_candidates(st), slot_names=["WR", "FLEX"], flex_types=["WR"],
        )
        changes = inseason.lineup_changes(st, best)
        self.assertEqual([c.action for c in changes], ["MOVE", "MOVE"])
        self.assertEqual(changes[1].slot_index, 1)
