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


class TestPlainLanguageDoc(unittest.TestCase):
    """The onboarding document must stay usable by a non-expert."""

    @classmethod
    def setUpClass(cls):
        raw = (DOCS / "how-this-works.md").read_text()
        cls.flat = " ".join(raw.split())

    def test_defines_every_position_abbreviation(self):
        for position in ("QB", "RB", "WR", "TE", "K", "DEF"):
            self.assertIn(position, self.flat)
        for term in ("quarterback", "running back", "wide receiver", "tight end", "kicker"):
            self.assertIn(term, self.flat.lower())

    def test_explains_the_central_trap(self):
        self.assertIn("value over replacement", self.flat.lower())
        self.assertIn("Do not take a quarterback early", self.flat)

    def test_states_the_bench_scores_zero(self):
        self.assertIn("score exactly zero", self.flat)

    def test_keeps_the_honest_caveat(self):
        self.assertIn("edge, not a guarantee", self.flat)

    def test_tells_the_operator_what_to_do(self):
        self.assertIn("scripts/draft.py", self.flat)
        self.assertIn("--manual", self.flat)


class TestDraftDayDoc(unittest.TestCase):
    """The one document that gets read under time pressure."""

    @classmethod
    def setUpClass(cls):
        cls.flat = " ".join((DOCS / "draft-day.md").read_text().split())

    def test_names_the_commands_to_run(self):
        for cmd in ("scripts/make_sheet.py", "scripts/serve.py"):
            self.assertIn(cmd, self.flat)

    def test_covers_every_failure_mode(self):
        for situation in ("clock is nearly out", "offline", "wrong player",
                          "off by one", "laptop is gone"):
            self.assertIn(situation, self.flat)

    def test_states_the_non_negotiables(self):
        self.assertIn("Kicker last", self.flat)
        self.assertIn("One quarterback", self.flat)
        self.assertIn("Never end the draft unable to fill a slot", self.flat)

    def test_explains_the_trap_warning(self):
        self.assertIn("low value", self.flat)
        self.assertIn("Do not take him", self.flat)

    def test_keeps_the_expectation_honest(self):
        self.assertIn("edge, not a guarantee", self.flat)
