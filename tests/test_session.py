"""Tests for the draft session state machine.

The session is what stands between a mistyped entry and a corrupted draft, so
these tests focus on the failure modes that would actually bite during a timed
draft: entries in the wrong place, a feed that dies mid-draft, a browser that
reloads, and advice that must never be unavailable.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from ffopt import config, pool, session


def _item(pid, name, pos, adp, payoff=200.0):
    return pool.Item(player_id=pid, name=name, pos=pos, team="TM",
                     payoff=payoff, adp=adp, games=17.0)


# Distinct surnames so a prefix cannot accidentally match several players, and
# enough depth at every type that a full 150-pick draft never exhausts the pool.
_NAMES = [
    "Ashford", "Brennan", "Calloway", "Danforth", "Ellsworth", "Fairbank",
    "Garrity", "Halloran", "Ingersoll", "Jessup", "Kirkland", "Lamonte",
    "Merriweather", "Northcott", "Ollivander", "Prescott", "Quimby", "Ravensworth",
    "Sutherland", "Thorncastle", "Underhill", "Vandermeer", "Wexley", "Yardley",
    "Zephyr", "Abernathy", "Blackwood", "Crowther", "Dunmore", "Eastcott",
    "Fenwick", "Grimsby", "Harrowgate", "Inglewood", "Jarrow", "Kettering",
    "Lockhart", "Mowbray", "Netherfield", "Oakhurst",
]


def _board():
    """A board deep enough that a full draft cannot exhaust any type."""
    items = []
    n = 1
    for pos, count, base in (("RB", 40, 300.0), ("WR", 40, 290.0), ("TE", 40, 200.0),
                             ("QB", 40, 320.0), ("K", 20, 80.0), ("DEF", 20, 110.0)):
        for i in range(count):
            items.append(
                _item(str(n), f"{_NAMES[i % len(_NAMES)]}{pos}{i}", pos,
                      float(n), base - i * 4)
            )
            n += 1
    return items


def _session(**kw):
    tmp = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    return session.DraftSession(board=_board(), path=tmp, **kw)


class TestDerivedState(unittest.TestCase):
    """Seat and ownership are derived from list position, never stored."""

    def setUp(self):
        self.s = _session()
        self.cfg = self.s.cfg

    def test_seat_on_clock_follows_snake_order(self):
        self.assertEqual(self.s.seat_on_clock(), 1)
        for i in range(self.cfg.num_agents):
            self.s.claim(str(i + 1))
        # Round 2 reverses, so the last seat picks again.
        self.assertEqual(self.s.seat_on_clock(), self.cfg.num_agents)

    def test_my_roster_is_derived_from_seat(self):
        self.s.set_seat(3)
        for i in range(5):
            self.s.claim(str(i + 1))
        self.assertEqual([p.player_id for p in self.s.my_roster()], ["3"])

    def test_changing_seat_recomputes_ownership(self):
        """The point of deriving rather than storing: no bookkeeping to go stale."""
        for i in range(5):
            self.s.claim(str(i + 1))
        self.s.set_seat(1)
        self.assertEqual([p.player_id for p in self.s.my_roster()], ["1"])
        self.s.set_seat(4)
        self.assertEqual([p.player_id for p in self.s.my_roster()], ["4"])

    def test_picks_until_my_turn(self):
        self.s.set_seat(5)
        self.assertEqual(self.s.picks_until_my_turn(), 4)
        for i in range(4):
            self.s.claim(str(i + 1))
        self.assertEqual(self.s.picks_until_my_turn(), 0)
        self.assertTrue(self.s.is_my_turn())

    def test_unfilled_slots_tracks_the_roster(self):
        self.s.set_seat(1)
        self.assertEqual(self.s.unfilled_slots()["QB"], 1)
        board = {i.player_id: i for i in self.s.board}
        qb = next(i for i in board.values() if i.pos == "QB")
        for n in range(1, self.cfg.pick_numbers(1)[0]):
            self.s.claim(str(n))
        self.s.claim(qb.player_id)
        self.assertNotIn("QB", self.s.unfilled_slots())


class TestCorrections(unittest.TestCase):
    """Recovering from a mis-entry mid-draft."""

    def setUp(self):
        self.s = _session()
        for i in range(5):
            self.s.claim(str(i + 1))

    def test_undo_removes_the_last_claim(self):
        self.s.undo()
        self.assertEqual(self.s.picks_made, 4)
        self.assertNotIn("5", self.s.claimed_ids)

    def test_undo_on_empty_is_safe(self):
        s = _session()
        self.assertIsNone(s.undo())

    def test_correct_replaces_in_place(self):
        self.s.correct(2, "40")
        self.assertEqual([c.player_id for c in self.s.claims][:3], ["1", "40", "3"])
        self.assertEqual(self.s.picks_made, 5)

    def test_correct_rejects_a_duplicate(self):
        with self.assertRaises(session.SessionError):
            self.s.correct(2, "4")

    def test_correct_allows_replacing_with_itself(self):
        self.s.correct(2, "2")
        self.assertEqual(self.s.claims[1].player_id, "2")

    def test_insert_shifts_later_picks(self):
        """The operation that rescues a session where a pick was never entered."""
        self.s.insert(3, "40")
        self.assertEqual([c.player_id for c in self.s.claims], ["1", "2", "40", "3", "4", "5"])
        self.assertEqual(self.s.picks_made, 6)

    def test_remove_shifts_later_picks_back(self):
        self.s.remove(2)
        self.assertEqual([c.player_id for c in self.s.claims], ["1", "3", "4", "5"])

    def test_insert_changes_who_owns_what(self):
        """A missed pick misattributes every later pick; insert must fix that."""
        self.s.set_seat(3)
        self.assertEqual([p.player_id for p in self.s.my_roster()], ["3"])
        self.s.insert(1, "40")
        self.assertEqual([p.player_id for p in self.s.my_roster()], ["2"])

    def test_out_of_range_operations_are_rejected(self):
        for bad in (0, 99):
            with self.assertRaises(session.SessionError):
                self.s.correct(bad, "40")
            with self.assertRaises(session.SessionError):
                self.s.remove(bad)

    def test_reset_clears_everything(self):
        self.s.reset()
        self.assertEqual(self.s.picks_made, 0)
        self.assertEqual(self.s.my_roster(), [])


class TestClaimValidation(unittest.TestCase):
    def setUp(self):
        self.s = _session()

    def test_unknown_player_rejected(self):
        with self.assertRaises(session.SessionError):
            self.s.claim("nope")

    def test_duplicate_rejected(self):
        self.s.claim("1")
        with self.assertRaises(session.SessionError):
            self.s.claim("1")

    def test_claiming_past_the_end_rejected(self):
        for i in range(self.s.total_picks):
            self.s.claim(str(i + 1))
        self.assertTrue(self.s.complete)
        with self.assertRaises(session.SessionError):
            self.s.claim("200")

    def test_invalid_seat_rejected(self):
        for bad in (0, 11, -1):
            with self.assertRaises(session.SessionError):
                self.s.set_seat(bad)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(session.SessionError):
            self.s.set_mode("telepathy")


class TestPersistence(unittest.TestCase):
    def test_survives_restart(self):
        s = _session()
        s.set_seat(7)
        s.set_mode("assisted")
        s.claim("1")
        s.claim("2")

        restored = session.DraftSession(board=_board(), path=s.path)
        self.assertTrue(restored.load())
        self.assertEqual(restored.seat, 7)
        self.assertEqual(restored.mode, "assisted")
        self.assertEqual(restored.picks_made, 2)

    def test_refuses_state_from_another_draft(self):
        import json
        s = _session()
        s.claim("1")
        data = json.loads(s.path.read_text())
        data["draft_id"] = "different"
        s.path.write_text(json.dumps(data))
        self.assertFalse(session.DraftSession(board=_board(), path=s.path).load())

    def test_corrupt_state_file_does_not_crash(self):
        s = _session()
        s.path.write_text("{ this is not json")
        self.assertFalse(session.DraftSession(board=_board(), path=s.path).load())

    def test_missing_file_is_not_an_error(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "absent.json"
        self.assertFalse(session.DraftSession(board=_board(), path=tmp).load())


class TestOfflineDegradation(unittest.TestCase):
    def test_sync_failure_preserves_the_board(self):
        s = _session()
        s.claim("1")
        s.claim("2")

        def boom(_draft_id):
            raise OSError("network is down")

        from ffopt import client
        original = client.draft_picks
        client.draft_picks = boom
        try:
            result = s.sync()
        finally:
            client.draft_picks = original

        self.assertFalse(result["ok"])
        self.assertEqual(s.picks_made, 2, "a failed sync must not lose entries")
        self.assertIn("network is down", s.last_sync_error)

    def test_sync_keeps_manual_entries_ahead_of_the_feed(self):
        """Assisted mode: the operator is deliberately ahead of a lagging feed."""
        s = _session()
        for i in range(5):
            s.claim(str(i + 1))

        from ffopt import client
        original = client.draft_picks
        client.draft_picks = lambda _d: [
            {"player_id": "1", "picked_by": "x"},
            {"player_id": "2", "picked_by": "x"},
        ]
        try:
            s.sync()
        finally:
            client.draft_picks = original
        self.assertEqual(s.picks_made, 5)
        self.assertEqual([c.player_id for c in s.claims], ["1", "2", "3", "4", "5"])

    def test_sync_adopts_the_feed_when_it_is_ahead(self):
        s = _session()
        s.claim("1")
        from ffopt import client
        original = client.draft_picks
        client.draft_picks = lambda _d: [
            {"player_id": str(i)} for i in range(1, 5)
        ]
        try:
            s.sync()
        finally:
            client.draft_picks = original
        self.assertEqual(s.picks_made, 4)


class TestAdvice(unittest.TestCase):
    def setUp(self):
        self.s = _session()
        self.s.set_seat(5)

    def test_panic_is_instant_and_non_empty(self):
        import time
        start = time.perf_counter()
        picks = self.s.panic()
        elapsed = time.perf_counter() - start
        self.assertTrue(picks)
        self.assertLess(elapsed, 0.5, "panic must not simulate anything")

    def test_panic_defers_the_worthless_types_early(self):
        """A panicked click must never spend an early pick on a kicker."""
        picks = self.s.panic(count=8)
        self.assertTrue(all(p["pos"] not in ("K", "DEF") for p in picks))

    def test_panic_forces_mandatory_slots_at_the_end(self):
        """With picks running out, only slot-filling players may be offered.

        Built by filling the board up to the operator's final pick, so the
        session genuinely has one pick left and an unfilled mandatory slot --
        the exact situation where a panicked click could otherwise draft a
        player at a position that is already full.
        """
        cfg = self.s.cfg
        mine = set(cfg.pick_numbers(5))
        final = max(mine)
        by_pos = {}
        for item in self.s.board:
            by_pos.setdefault(item.pos, []).append(item)
        cursor = {p: 0 for p in by_pos}

        def take(pos):
            while cursor[pos] < len(by_pos[pos]):
                item = by_pos[pos][cursor[pos]]
                cursor[pos] += 1
                if item.player_id not in self.s.claimed_ids:
                    return item
            raise AssertionError(f"exhausted {pos}")

        # Our picks fill every slot except kicker; everyone else takes RB/WR.
        my_plan = ["RB", "RB", "WR", "WR", "TE", "QB", "DEF",
                   "RB", "WR", "RB", "WR", "TE", "RB", "WR"]
        step = 0
        for pick_no in range(1, final):
            if pick_no in mine:
                self.s.claim(take(my_plan[step]).player_id)
                step += 1
            else:
                # Cycle filler across the deep types so none is exhausted.
                self.s.claim(take(["RB", "WR", "TE", "QB"][pick_no % 4]).player_id)

        need = set(self.s.unfilled_slots())
        self.assertEqual(need, {"K"}, f"expected only K unfilled, got {need}")
        picks = self.s.panic()
        self.assertTrue(picks)
        self.assertTrue(all(p["pos"] == "K" for p in picks),
                        f"expected only K, got {[p['pos'] for p in picks]}")

    def test_panic_never_offers_a_claimed_player(self):
        self.s.claim("1")
        self.assertNotIn("1", [p["player_id"] for p in self.s.panic(count=20)])

    def test_recommendations_fall_back_when_seat_unknown(self):
        s = _session()
        self.assertTrue(s.recommendations())

    def test_recommendations_never_raise(self):
        """Advice must be available even if the optimizer fails."""
        from ffopt import optimizer
        original = optimizer.recommend

        def boom(*a, **k):
            raise RuntimeError("optimizer exploded")

        optimizer.recommend = boom
        try:
            picks = self.s.recommendations()
        finally:
            optimizer.recommend = original
        self.assertTrue(picks, "must degrade to the panic list, not fail")

    def test_snapshot_is_json_serialisable(self):
        import json
        json.dumps(self.s.snapshot())


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.s = _session()

    def test_search_excludes_claimed_players(self):
        name = self.s.board[0].name
        first = self.s.search(name)
        self.assertEqual(len(first), 1)
        claimed_id = first[0]["player_id"]
        self.s.claim(claimed_id)
        # Fuzzy matching may still offer near-misses; what matters is that the
        # claimed player can never be offered again.
        self.assertNotIn(claimed_id, [r["player_id"] for r in self.s.search(name)])

    def test_find_claimed_identifies_a_duplicate(self):
        name = self.s.board[0].name
        first = self.s.search(name)[0]
        self.s.claim(first["player_id"])
        found = self.s.find_claimed(name)
        self.assertIsNotNone(found)
        self.assertEqual(found.player_id, first["player_id"])

    def test_find_claimed_returns_none_on_a_clean_board(self):
        self.assertIsNone(self.s.find_claimed(self.s.board[0].name))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestAssistedMode(unittest.TestCase):
    """Assisted mode proposes; it must never silently overwrite the operator."""

    def setUp(self):
        self.s = _session()
        self.s.set_mode("assisted")

    def _feed(self, ids):
        from ffopt import client
        original = client.draft_picks
        client.draft_picks = lambda _d: [{"player_id": i} for i in ids]
        return original

    def test_propose_reports_additions_without_applying(self):
        from ffopt import client
        original = self._feed(["1", "2", "3"])
        try:
            result = self.s.propose()
        finally:
            client.draft_picks = original
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["additions"]), 3)
        self.assertEqual(self.s.picks_made, 0, "propose must not mutate the board")

    def test_propose_detects_a_disagreement(self):
        from ffopt import client
        self.s.claim("5")
        original = self._feed(["1"])
        try:
            result = self.s.propose()
        finally:
            client.draft_picks = original
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["pick"], 1)
        self.assertEqual(self.s.claims[0].player_id, "5", "local entry must survive")

    def test_accepting_a_proposal_applies_it(self):
        from ffopt import client
        original = self._feed(["1", "2"])
        try:
            self.s.propose()
            self.assertEqual(self.s.picks_made, 0)
            self.s.accept_proposal()
        finally:
            client.draft_picks = original
        self.assertEqual(self.s.picks_made, 2)

    def test_propose_survives_a_dead_feed(self):
        from ffopt import client
        self.s.claim("1")
        original = client.draft_picks
        client.draft_picks = lambda _d: (_ for _ in ()).throw(OSError("down"))
        try:
            result = self.s.propose()
        finally:
            client.draft_picks = original
        self.assertFalse(result["ok"])
        self.assertEqual(self.s.picks_made, 1)

    def test_no_proposal_when_already_in_sync(self):
        from ffopt import client
        self.s.claim("1")
        self.s.claim("2")
        original = self._feed(["1", "2"])
        try:
            result = self.s.propose()
        finally:
            client.draft_picks = original
        self.assertEqual(result["additions"], [])
        self.assertEqual(result["conflicts"], [])


class TestUnlistedPlayers(unittest.TestCase):
    """A real draft takes players outside the consensus board in late rounds.

    Dropping them would shorten the claim list, and since a claim's position is
    its pick number, every later pick would be attributed to the wrong seat.
    That corrupts whose roster is whose with no visible symptom, so it is
    guarded explicitly.
    """

    def setUp(self):
        self.s = _session()
        self.s.set_seat(3)

    def _feed(self, ids):
        from ffopt import client
        original = client.draft_picks
        client.draft_picks = lambda _d: [{"player_id": i} for i in ids]
        return original

    def test_unknown_player_does_not_shift_later_picks(self):
        from ffopt import client
        original = self._feed(["1", "NOT_ON_OUR_BOARD", "3", "4"])
        try:
            self.s.sync()
        finally:
            client.draft_picks = original
        self.assertEqual(self.s.picks_made, 4)
        self.assertEqual([p.player_id for p in self.s.my_roster()], ["3"])

    def test_unknown_player_is_visible_to_the_operator(self):
        from ffopt import client
        original = self._feed(["1", "NOT_ON_OUR_BOARD"])
        try:
            self.s.sync()
        finally:
            client.draft_picks = original
        names = [r["name"] for r in self.s.snapshot()["recent"]]
        self.assertTrue(any("unlisted" in n for n in names), names)

    def test_snapshot_with_unknown_players_is_serialisable(self):
        import json
        from ffopt import client
        original = self._feed(["1", "MYSTERY", "3"])
        try:
            self.s.sync()
        finally:
            client.draft_picks = original
        json.dumps(self.s.snapshot())

    def test_propose_handles_unknown_players(self):
        from ffopt import client
        original = self._feed(["1", "MYSTERY"])
        try:
            result = self.s.propose()
        finally:
            client.draft_picks = original
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["additions"]), 2)

    def test_manual_entry_still_rejects_unknown_players(self):
        """The feed may contain them; the operator must not be able to type one."""
        with self.assertRaises(session.SessionError):
            self.s.claim("NOT_ON_OUR_BOARD")
