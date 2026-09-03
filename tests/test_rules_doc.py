"""Guards the rules documentation against drifting from its YAML source.

docs/league-rules.yaml is generated from the API and is the source of truth.
docs/league-rules.md explains it in prose for a reader with no football
knowledge. The prose is worth writing by hand, but every number in it must
still agree with the YAML, or the document silently becomes fiction.
"""

import pathlib
import re
import unittest

import yaml

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"


class TestRulesDocMatchesSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = yaml.safe_load((DOCS / "league-rules.yaml").read_text())
        raw = (DOCS / "league-rules.md").read_text()
        cls.flat = " ".join(raw.split())

    def test_league_identity(self):
        self.assertIn(self.data["league"]["name"], self.flat)
        self.assertIn(self.data["league"]["league_id"], self.flat)

    def test_agent_and_round_counts(self):
        self.assertIn(f"{self.data['league']['total_agents']} agents", self.flat)
        self.assertIn(f"Rounds | {self.data['draft']['rounds']}", self.flat)

    def test_pick_timer(self):
        self.assertIn(f"{self.data['draft']['pick_timer_seconds']}-second", self.flat)

    def test_scoring_weight_count(self):
        self.assertIn(f"All {len(self.data['scoring_weights'])} weights", self.flat)

    def test_playoff_qualifiers(self):
        ss = self.data["season_structure"]
        self.assertIn(f"{ss['playoff_teams']} of {self.data['league']['total_agents']}", self.flat)

    def test_starting_lineup_is_spelled_out(self):
        """The exact slot string must appear, so the reader can copy it."""
        ordered = self.data["roster_constraints"]["roster_positions_ordered"]
        starters = ", ".join(p for p in ordered if p != "BN")
        self.assertIn(starters, self.flat, "starting lineup slots not listed verbatim")

    def test_bench_and_roster_size(self):
        rc = self.data["roster_constraints"]
        self.assertIn(f"Bench ({rc['bench_slots']} slots)", self.flat)
        self.assertIn(f"Total roster: {rc['total_roster_size']} items", self.flat)

    def test_flex_count_is_stated(self):
        flex = self.data["roster_constraints"]["starting_slots"]["FLEX"]
        self.assertIn(f"**{flex}**", self.flat)

    def test_key_scoring_weights_quoted_correctly(self):
        weights = self.data["scoring_weights"]
        self.assertIn(f"`rec: {weights['rec']}`", self.flat)
        self.assertIn(f"`pass_td: {weights['pass_td']}`", self.flat)
        self.assertIn(f"`rush_td: {weights['rush_td']}`", self.flat)

    def test_records_no_keepers(self):
        declared = [a for a in self.data["agents"] if a.get("keepers")]
        self.assertEqual(declared, [], "a keeper now exists; the doc must be updated")
        self.assertIn("None declared", self.flat)

    def test_records_unassigned_draft_order(self):
        if not self.data["draft"]["draft_order_assigned"]:
            self.assertIn("NOT YET ASSIGNED", self.flat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
