"""Tests for manual board entry.

This is the fallback if the live pick feed fails during the draft, so it has to
work first time under a 60 second timer. The tests focus on the two ways it
could fail live: a lookup that silently matches the wrong player, and a board
that is lost when the tool restarts.
"""

import pathlib
import tempfile
import unittest

from ffopt import config, manual, pool


def _item(pid, name, pos, adp):
    return pool.Item(player_id=pid, name=name, pos=pos, team="X",
                     payoff=100.0, adp=adp, games=17.0)


ROSTER = [
    _item("1", "Jahmyr Gibbs", "RB", 2.0),
    _item("2", "Bijan Robinson", "RB", 2.5),
    _item("3", "Puka Nacua", "WR", 4.0),
    _item("4", "Samson Nacua", "WR", None),
    _item("5", "Josh Allen", "QB", 21.0),
    _item("6", "Keenan Allen", "WR", 199.0),
    _item("7", "Los Angeles Rams", "DEF", 87.0),
    _item("8", "Brock Bowers", "TE", 24.0),
    _item("9", "Nick Bowers", "TE", None),
]


class TestLookup(unittest.TestCase):
    def _find(self, q):
        return manual.find(q, ROSTER)

    def test_surname_resolves(self):
        self.assertEqual(self._find("gibbs").exact.name, "Jahmyr Gibbs")

    def test_case_and_whitespace_insensitive(self):
        for q in ("GIBBS", "  Gibbs  ", "gIbBs"):
            self.assertEqual(self._find(q).exact.name, "Jahmyr Gibbs")

    def test_first_name_resolves(self):
        self.assertEqual(self._find("bijan").exact.name, "Bijan Robinson")

    def test_undraftable_namesake_does_not_cause_ambiguity(self):
        """The speed-critical case: one plausible player, one who never goes."""
        self.assertEqual(self._find("nacua").exact.name, "Puka Nacua")
        self.assertEqual(self._find("bowers").exact.name, "Brock Bowers")

    def test_genuine_collision_stays_ambiguous(self):
        """Two draftable Allens must NOT be silently resolved."""
        m = self._find("allen")
        self.assertTrue(m.ambiguous)
        self.assertEqual(m.candidates[0].name, "Josh Allen")  # best consensus first

    def test_team_defense_matches_nickname_and_city(self):
        for q in ("rams", "los angeles rams"):
            self.assertEqual(self._find(q).exact.pos, "DEF")

    def test_initial_plus_surname(self):
        self.assertEqual(self._find("j gibbs").exact.name, "Jahmyr Gibbs")

    def test_misspelling_is_tolerated(self):
        self.assertEqual(manual.find("gibs", ROSTER).exact.name, "Jahmyr Gibbs")

    def test_nonsense_returns_empty(self):
        self.assertTrue(self._find("zzzznotaplayer").empty)

    def test_blank_returns_empty(self):
        self.assertTrue(self._find("   ").empty)


class TestBoard(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "board.json"
        self.board = manual.ManualBoard(self.cfg, self.tmp)

    def test_claim_and_undo(self):
        self.board.claim(ROSTER[0])
        self.board.claim(ROSTER[1], mine=True)
        self.assertEqual(self.board.picks_made, 2)
        self.assertEqual([i.name for i in self.board.my_roster(ROSTER)], ["Bijan Robinson"])
        self.board.undo()
        self.assertEqual(self.board.picks_made, 1)
        self.assertEqual(self.board.my_roster(ROSTER), [])

    def test_undo_on_empty_board_is_safe(self):
        self.assertIsNone(self.board.undo())

    def test_survives_restart(self):
        """A crash mid-draft must not lose the board."""
        self.board.claim(ROSTER[0])
        self.board.claim(ROSTER[2], mine=True)
        restored = manual.ManualBoard(self.cfg, self.tmp)
        self.assertTrue(restored.load())
        self.assertEqual(restored.picks_made, 2)
        self.assertEqual([i.name for i in restored.my_roster(ROSTER)], ["Puka Nacua"])

    def test_refuses_a_board_from_a_different_draft(self):
        self.board.claim(ROSTER[0])
        import json
        data = json.loads(self.tmp.read_text())
        data["draft_id"] = "some-other-draft"
        self.tmp.write_text(json.dumps(data))
        self.assertFalse(manual.ManualBoard(self.cfg, self.tmp).load())

    def test_seat_on_clock_cycles(self):
        self.assertEqual(self.board.seat_on_clock(), 1)
        for _ in range(3):
            self.board.claim(ROSTER[0])
        self.assertEqual(self.board.seat_on_clock(), 4)

    def test_seed_from_partial_feed(self):
        """If the feed dies mid-draft, keep what it already returned."""
        picks = [
            {"player_id": "1", "picked_by": "someone"},
            {"player_id": "3", "picked_by": self.cfg.my_user_id},
        ]
        n = manual.sync_from_feed(self.board, picks, self.cfg.my_user_id)
        self.assertEqual(n, 2)
        self.assertEqual([i.name for i in self.board.my_roster(ROSTER)], ["Puka Nacua"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
