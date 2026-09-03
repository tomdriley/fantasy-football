"""Tests for the printable fallback sheet.

The sheet is the last line of defence if the tooling fails during a timed
draft, so its correctness matters more than its formatting. These tests check
that it contains the information needed to draft without a computer.
"""

import unittest

from ffopt import cheatsheet, client, config, pool


class TestCheatSheet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load()
        items = pool.build(client.projections(cls.cfg.season), cls.cfg.scoring_weights)
        cls.text = cheatsheet.render(
            [i for i in items if i.adp is not None], cls.cfg
        )

    def test_covers_every_type(self):
        for position in ("QB", "RB", "WR", "TE", "K", "DEF"):
            self.assertIn(position, self.text)

    def test_lists_pick_numbers_for_every_seat(self):
        """Draft order is unassigned, so all 10 seats must be covered."""
        for seat in range(1, self.cfg.num_agents + 1):
            picks = self.cfg.pick_numbers(seat)[:8]
            row = " ".join(f"{p:>3}" for p in picks)
            self.assertIn(row, self.text, f"seat {seat} schedule missing")

    def test_states_the_deferral_rules(self):
        self.assertIn("Kicker: LAST pick", self.text)
        self.assertIn("Defense: second to last", self.text)

    def test_warns_about_the_quarterback_trap(self):
        self.assertIn("barely better than the 10th best", self.text)

    def test_insists_every_slot_is_filled(self):
        """Previously this asked the reader which waiver mode applied.

        Backtesting removed the ambiguity: filling every mandatory slot wins in
        both worlds, so the sheet now states a rule instead of a contingency.
        """
        self.assertIn("NEVER end the draft unable to fill a slot", self.text)

    def test_marks_tier_cliffs(self):
        self.assertIn("<- cliff", self.text)

    def test_explains_bench_scores_nothing(self):
        self.assertIn("score NOTHING unless started", self.text)

    def test_fits_printable_width(self):
        for line in self.text.splitlines():
            self.assertLessEqual(len(line), 200, line[:60])


if __name__ == "__main__":
    unittest.main(verbosity=2)
