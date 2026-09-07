import copy
from unittest.mock import patch

import yaml

from ffopt import advice, archive, client, collector
from ffopt.evaluation import evaluate_snapshot
from ffopt.service import AdvisorService
from tests.test_archive import ArchiveCase, EPOCH


class TestManagerAdvice(ArchiveCase):
    def setUp(self):
        super().setUp()
        self.rules = self.folder / "rules.yaml"
        self.rules.write_text(yaml.safe_dump(self.cfg.raw))
        self.service = AdvisorService(self.store.path, self.folder / "jobs.sqlite3", self.rules)

    def saved_advice(self, **kwargs):
        sid = self.capture(**kwargs)
        evaluate_snapshot(self.store, sid)
        return sid

    def test_empty_state_is_not_all_set(self):
        result = self.service.advice()
        self.assertIsNone(result["snapshot"])
        self.assertFalse(result["freshness"]["usable"])
        self.assertEqual(result["lineup"], [])
        self.assertIsNone(result["summary"]["projected_total"])

    def test_current_uses_latest_baseline_not_research_selection(self):
        first = self.saved_advice()
        self.transport.clock += 1000
        self.transport.projections[0]["stats"]["rec"] = 20
        second = self.saved_advice(refresh=True)
        evaluate_snapshot(self.store, second, 1000)  # Removes all shadow pickups, not baseline pickups.
        view = self.service.advice()
        self.assertEqual(view["snapshot"]["id"], second)
        self.assertTrue(view["freshness"]["usable"])
        self.assertEqual(len(view["pickups"]), 1)
        historical = self.service.advice(first)
        self.assertEqual(historical["mode"], "historical")
        self.assertFalse(historical["freshness"]["usable"])
        evaluate_snapshot(self.store, first, 2)
        self.assertEqual(self.service.advice()["snapshot"]["id"], second)

    def test_old_advice_is_reference_only(self):
        self.saved_advice()
        self.transport.clock += advice.RECENCY_SECONDS * 1000
        view = self.service.advice()
        self.assertEqual(view["freshness"]["state"], "stale")
        self.assertFalse(view["freshness"]["usable"])
        self.assertTrue(all(a["blocked"] for a in view["actions"]))
        self.assertEqual(view["summary"]["headline"], "Update advice before acting")

    def test_recent_capture_with_old_reused_references_is_not_recent_advice(self):
        self.saved_advice()
        self.transport.clock += advice.RECENCY_SECONDS * 1000 + 1
        self.saved_advice()  # Daily player reference is reused.
        view = self.service.advice()
        self.assertEqual(view["freshness"]["age_ms"], 0)
        self.assertEqual(view["freshness"]["state"], "stale")

    def test_kickoff_after_capture_requires_update_even_within_recency_window(self):
        self.transport.payloads[f"/scores/nfl/regular/{self.cfg.season}/1"][0]["start_time"] = EPOCH + 60_000
        self.saved_advice()
        view = self.service.advice()
        self.assertEqual(view["freshness"]["valid_until_ms"], EPOCH + 60_000)
        self.transport.clock += 60_001
        view = self.service.advice()
        self.assertEqual(view["freshness"]["state"], "locks_changed")
        self.assertFalse(view["freshness"]["usable"])

    def test_failed_update_does_not_relabel_previous_advice_as_current(self):
        previous = self.saved_advice()
        self.transport.clock += 1000
        self.transport.fail_path = "/v1/players/nfl"
        with self.assertRaises(collector.CaptureError) as caught:
            self.capture(refresh=True)
        view = self.service.advice()
        self.assertEqual(view["snapshot"]["id"], previous)
        self.assertEqual(view["latest_attempt"]["id"], caught.exception.snapshot_id)
        self.assertTrue(view["latest_attempt"]["errors"])
        self.assertFalse(view["freshness"]["usable"])

    def test_complete_capture_without_analysis_retains_prior_advice_as_reference(self):
        prior = self.saved_advice()
        self.transport.clock += 1000
        pending_analysis = self.capture()
        view = self.service.advice()
        self.assertEqual(view["snapshot"]["id"], prior)
        self.assertEqual(view["latest_attempt"]["id"], pending_analysis)
        self.assertFalse(view["latest_attempt"]["analysis_ready"])
        self.assertFalse(view["freshness"]["usable"])

    def test_changed_rules_do_not_reinterpret_old_projections(self):
        self.saved_advice()
        before = self.service.advice()["summary"]["projected_total"]
        changed = copy.deepcopy(self.cfg.raw)
        changed["scoring_weights"]["rec"] = 2
        self.rules.write_text(yaml.safe_dump(changed))
        view = self.service.advice()
        self.assertEqual(view["freshness"]["state"], "rules_changed")
        self.assertEqual(view["summary"]["projected_total"], before)

    def test_changed_flex_eligibility_invalidates_saved_advice(self):
        self.saved_advice()
        changed = copy.deepcopy(self.cfg.raw)
        changed["roster_constraints"]["flex_accepts_types"] = ["RB"]
        self.rules.write_text(yaml.safe_dump(changed))
        view = self.service.advice()
        self.assertEqual(view["freshness"]["state"], "rules_changed")
        self.assertFalse(view["freshness"]["usable"])

    def test_missing_starter_forecast_is_not_a_bench_instruction_or_zero_total(self):
        self.transport.projections[:] = [
            p for p in self.transport.projections if p["player_id"] != "p0"
        ]
        self.saved_advice()
        view = self.service.advice()
        self.assertFalse(view["freshness"]["usable"])
        self.assertIn("not a reason to bench", view["freshness"]["message"])
        self.assertIsNone(view["summary"]["projected_total"])
        self.assertTrue(all(a["blocked"] for a in view["actions"]))
        instructions = [s for a in view["actions"] for s in a["instructions"]]
        self.assertNotIn("Bench Player 0.", instructions)

    def test_pickup_kickoff_expires_advice_even_before_our_own_games(self):
        games = self.transport.payloads[f"/scores/nfl/regular/{self.cfg.season}/1"]
        games[0]["metadata"]["away_team"] = "CAR"
        games.append({
            "game_id": "other", "start_time": EPOCH + 60_000,
            "metadata": {"home_team": "GB", "away_team": "CHI"},
        })
        self.saved_advice()
        first = self.service.advice()
        self.assertEqual(first["freshness"]["valid_until_ms"], EPOCH + 60_000)
        self.assertTrue(first["pickups"])
        self.transport.clock += 60_001
        view = self.service.advice()
        self.assertFalse(view["freshness"]["usable"])
        self.assertIn("pickup", view["freshness"]["message"])

    def test_questionable_is_a_check_not_a_bench_instruction(self):
        self.transport.players["p0"]["injury_status"] = "Questionable"
        self.saved_advice()
        view = self.service.advice()
        self.assertEqual(view["summary"]["injury_count"], 1)
        self.assertEqual(view["summary"]["lineup_change_count"], 0)
        self.assertEqual(view["injuries"][0]["player_id"], "p0")
        self.assertEqual(view["lineup"][0]["change"], "same")
        self.assertFalse(any("Bench" in text for a in view["actions"] for text in a["instructions"]))

    def test_availability_has_a_check_time_before_the_game_and_a_clear_return_instruction(self):
        self.transport.players["p0"]["injury_status"] = "Questionable"
        self.saved_advice()
        view = self.service.advice()
        player = view["injuries"][0]
        self.assertEqual(player["check_at_ms"], player["kickoff_ms"] - 90 * 60_000)
        self.assertIn("Return about 90 minutes", player["note"])
        self.assertIn("Update advice", player["note"])
        self.assertEqual(view["summary"]["headline"], "No lineup changes suggested right now")

    def test_availability_changes_to_check_now_inside_the_pregame_window(self):
        self.transport.players["p0"]["injury_status"] = "Questionable"
        self.transport.clock += 23 * 60 * 60_000
        self.saved_advice()
        view = self.service.advice()
        self.assertTrue(view["freshness"]["usable"])
        self.assertEqual(view["summary"]["headline"], "Check player availability now")

    def test_locked_out_player_does_not_produce_unqualified_all_set(self):
        self.transport.payloads[f"/scores/nfl/regular/{self.cfg.season}/1"][0]["start_time"] = EPOCH - 1
        self.transport.players["p0"]["injury_status"] = "Out"
        self.saved_advice()
        view = self.service.advice()
        self.assertTrue(view["lineup"][0]["locked_at_capture"])
        self.assertTrue(any("already locked" in text for a in view["actions"] for text in a["instructions"]))
        self.assertEqual(view["summary"]["headline"], "Your lineup needs attention")

    def test_optional_pickup_does_not_become_primary_action(self):
        self.saved_advice()
        view = self.service.advice()
        self.assertTrue(view["pickups"])
        self.assertEqual(view["actions"], [])
        self.assertEqual(view["summary"]["headline"], "No lineup changes suggested")

    def test_reads_are_offline_and_do_not_create_evaluations(self):
        sid = self.saved_advice()
        count = len(self.store.evaluations(sid))
        with patch.object(client, "fetch_document", side_effect=AssertionError("network")):
            first = self.service.advice()
            second = self.service.advice()
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.evaluations(sid)), count)
        cached = self.service._advice_context(sid)
        self.assertNotIn("free-def", cached["players"])
        self.assertEqual(len(cached["players"]), len(self.cfg.starting_positions))

    def test_historical_view_does_not_depend_on_current_rules_file(self):
        sid = self.saved_advice()
        self.rules.unlink()
        view = self.service.advice(sid)
        self.assertEqual(view["mode"], "historical")
        self.assertEqual(view["snapshot"]["id"], sid)

    def test_historical_deadline_is_the_one_known_at_capture(self):
        sid = self.saved_advice()
        self.transport.clock += 2 * 86_400_000
        current = self.service.advice()
        self.assertIsNone(current["next_deadline"])
        historical = self.service.advice(sid)
        self.assertEqual(historical["next_deadline"]["at_ms"], EPOCH + 86_400_000)

    def test_cancelled_team_does_not_remove_other_players_shared_deadline(self):
        sid = self.saved_advice()
        cfg, state = collector.replay(self.store, sid)
        state.players["p0"] = {**state.players["p0"], "team": "GB"}
        state.unavailable_teams.add("GB")
        facts = advice.context(cfg, state)
        self.assertEqual(len(facts["waves"]), 1)
        self.assertNotIn("p0", [p["player_id"] for p in facts["waves"][0]["players"]])
        self.assertIn("p1", [p["player_id"] for p in facts["waves"][0]["players"]])
