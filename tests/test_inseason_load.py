import copy
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from ffopt import client, config, inseason


class TestSnapshotLoad(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()
        self.mine = {
            "roster_id": 2, "owner_id": self.cfg.my_user_id,
            "players": ["mine"], "reserve": [], "starters": ["mine"] + ["0"] * 9,
        }
        self.rosters = [self.mine] + [
            {"roster_id": i, "owner_id": f"owner{i}", "players": [str(i)]}
            for i in range(1, 11) if i != 2
        ]
        self.matches = [{
            "roster_id": 2, "starters": ["mine"] + ["0"] * 9, "matchup_id": 1,
        }]
        self.league = {
            "season": self.cfg.season, "status": "in_season",
            "scoring_settings": self.cfg.scoring_weights,
            "roster_positions": self.cfg.raw["roster_constraints"]["roster_positions_ordered"],
            "settings": self.cfg.in_season["raw_settings"].copy(),
        }
        self.players = {"mine": {
            "position": "QB", "fantasy_positions": ["QB"], "team": "DET", "full_name": "Mine",
        }}
        self.games = [{
            "game_id": "game", "start_time": 4102444800000,
            "metadata": {"home_team": "DET", "away_team": "GB"},
        }]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = pathlib.Path(tmp.name) / "response.json"
        cache.write_text("{}")
        responses = {
            "get": self.league, "rosters": self.rosters, "matchups": self.matches,
            "players": self.players, "scores": self.games, "transactions": [],
            "nfl_state": {"season": self.cfg.season, "week": 1},
            "weekly_projections": [{"player_id": "mine", "stats": {"pass_yd": 200}}],
            "_cache_path": cache,
        }
        self.mocks = {}
        for name, response in responses.items():
            p = patch.object(client, name, return_value=response)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def test_preserves_slot_order_and_exclusive_reserve_ownership(self):
        self.rosters[1]["reserve"] = ["injured-other"]
        snapshot = inseason.load(self.cfg, 1, refresh=True)
        self.assertEqual(snapshot.my_starters, self.matches[0]["starters"])
        self.assertIn("injured-other", snapshot.rostered_elsewhere)
        self.assertFalse(snapshot.is_free("injured-other"))
        self.assertEqual(inseason.candidate(snapshot, "mine").current_slot, 0)
        self.mocks["players"].assert_called_once_with(refresh=True)

    def test_rejects_drifted_rules(self):
        self.league["settings"]["trade_deadline"] = 5
        with self.assertRaisesRegex(inseason.StateError, "setting"):
            inseason.load(self.cfg, 1)

    def test_refuses_missing_rosters_or_duplicate_ownership(self):
        self.rosters.pop()
        with self.assertRaisesRegex(inseason.StateError, "incomplete"):
            inseason.load(self.cfg, 1)
        self.rosters.append({"roster_id": 10, "players": ["mine"]})
        with self.assertRaisesRegex(inseason.StateError, "multiple rosters"):
            inseason.load(self.cfg, 1)

    def test_refuses_previous_week_with_current_rosters(self):
        with self.assertRaisesRegex(inseason.StateError, "current"):
            inseason.load(self.cfg, 2)

    def test_missing_starter_slots_not_compressed(self):
        self.matches[0]["starters"] = ["mine"]
        with self.assertRaisesRegex(inseason.StateError, "ordered"):
            inseason.load(self.cfg, 1)

    def test_ineligible_ir_occupant_blocks_moves_but_explains(self):
        self.mine["reserve"] = ["healthy"]
        self.players["healthy"] = {
            "position": "WR", "team": "DET", "full_name": "Healthy",
        }
        snapshot = inseason.load(self.cfg, 1)
        self.assertIn("healthy", snapshot.reserve)
        self.assertTrue(any("activate Healthy" in w for w in snapshot.move_warnings))
