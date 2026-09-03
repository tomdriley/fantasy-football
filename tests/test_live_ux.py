"""Tests for the live advisor's rendered states.

These pin behaviour that is easy to get wrong under time pressure and hard to
notice in review: the tool must never recommend a pick the operator cannot make,
and must never display a pick number past the end of the draft.
"""

import unittest

from ffopt import config, live, optimizer, pool


def _item(name, pos, adp, payoff=200.0):
    return pool.Item(player_id=name, name=name, pos=pos, team="X",
                     payoff=payoff, adp=adp, games=17.0)


class TestLiveRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load()
        cls.board = [_item(f"P{i}", "RB", float(i)) for i in range(1, 40)]
        cls.recs = [
            optimizer.Recommendation(item=cls.board[i], expected_lineup_value=100.0 - i,
                                     immediate_value=10.0)
            for i in range(3)
        ]
        cls.vor = {i.player_id: 50.0 for i in cls.board}

    def _render(self, picks_made, roster):
        state = live.BoardState(claimed=set(), my_roster=roster, picks_made=picks_made,
                                my_seat=5, on_the_clock=(picks_made % 10) + 1)
        return live.render(self.cfg, self.board, state, 5, self.recs, self.vor)

    def test_full_roster_stops_recommending(self):
        """The bug this guards: 15/15 taken while still advising a pick."""
        roster = [_item(f"R{i}", "RB", float(i)) for i in range(self.cfg.rounds)]
        text = self._render(140, roster)
        self.assertIn("DRAFT COMPLETE", text)
        self.assertNotIn("<= TAKE", text)

    def test_full_roster_warns_about_unfillable_slots(self):
        roster = [_item(f"R{i}", "RB", float(i)) for i in range(self.cfg.rounds)]
        text = self._render(140, roster)
        self.assertIn("WARNING", text)
        for position in ("QB", "TE", "K", "DEF"):
            self.assertIn(position, text)

    def test_never_shows_a_pick_past_the_end(self):
        total = self.cfg.rounds * self.cfg.num_agents
        text = self._render(total, [])
        self.assertIn("DRAFT COMPLETE", text)
        self.assertNotIn(f"pick {total + 1}", text)

    def test_my_turn_reports_the_gap_to_the_next_turn(self):
        picks = self.cfg.pick_numbers(5)
        text = self._render(picks[0] - 1, [])
        self.assertIn("YOUR PICK", text)
        self.assertIn(f"next turn is pick {picks[1]}", text)

    def test_waiting_reports_when_our_turn_arrives(self):
        picks = self.cfg.pick_numbers(5)
        text = self._render(picks[0], [])
        self.assertIn("waiting", text)
        self.assertIn(f"your next pick is {picks[1]}", text)

    def test_shows_exactly_three_options(self):
        text = self._render(4, [])
        self.assertEqual(text.count("<= TAKE"), 1)
        rows = [l for l in text.splitlines() if l.strip().startswith(("1  ", "2  ", "3  "))]
        self.assertEqual(len(rows), 3)

    def test_reports_stakes_and_sanity(self):
        text = self._render(4, [])
        self.assertIn("STAKES", text)
        self.assertIn("SANITY", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
