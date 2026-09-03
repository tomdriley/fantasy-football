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

from ffopt import client, config, pool, session


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


class TestInvariantsUnderMixedOperations(unittest.TestCase):
    """The board must stay self-consistent however it is edited.

    Because a claim's position is its pick number, an off-by-one anywhere
    reassigns players to the wrong seats. That failure is silent, so it is
    checked directly: the roster derived from the seat must always equal the
    claims sitting at that seat's pick positions, and no player may appear
    twice.
    """

    def _check(self, s):
        mine = set(s.cfg.pick_numbers(s.seat))
        expected = [
            c.player_id for n, c in enumerate(s.claims, 1)
            if n in mine and c.player_id in s._by_id
        ]
        self.assertEqual([p.player_id for p in s.my_roster()], expected)
        self.assertEqual(len(s.claimed_ids), len(s.claims), "duplicate claim")

    def test_invariant_holds_through_an_edit_sequence(self):
        s = _session()
        s.set_seat(4)
        for i in range(1, 9):
            s.claim(str(i))
        self._check(s)
        for step in (lambda: s.undo(),
                     lambda: s.correct(2, "50"),
                     lambda: s.insert(1, "60"),
                     lambda: s.remove(3),
                     lambda: s.set_seat(9),
                     lambda: s.set_seat(4)):
            step()
            self._check(s)

    def test_invariant_holds_through_random_operations(self):
        import random
        s = _session()
        s.set_seat(4)
        rng = random.Random(7)
        for _ in range(400):
            roll = rng.random()
            try:
                if roll < 0.55 or s.picks_made == 0:
                    free = [i for i in s.board if i.player_id not in s.claimed_ids]
                    if free and not s.complete:
                        s.claim(rng.choice(free).player_id)
                elif roll < 0.70:
                    s.undo()
                elif roll < 0.80:
                    free = [i for i in s.board if i.player_id not in s.claimed_ids]
                    if free:
                        s.correct(rng.randint(1, s.picks_made),
                                  rng.choice(free).player_id)
                elif roll < 0.90:
                    free = [i for i in s.board if i.player_id not in s.claimed_ids]
                    if free:
                        s.insert(rng.randint(1, s.picks_made + 1),
                                 rng.choice(free).player_id)
                else:
                    s.remove(rng.randint(1, s.picks_made))
            except session.SessionError:
                pass  # rejected operations are fine; corruption is not
        self._check(s)

    def test_invariant_holds_across_syncs(self):
        from ffopt import client
        s = _session()
        s.set_seat(4)
        for i in range(1, 9):
            s.claim(str(i))
        original = client.draft_picks
        try:
            client.draft_picks = lambda _d: [{"player_id": str(i)} for i in range(1, 4)]
            s.sync()
            self._check(s)
            client.draft_picks = lambda _d: [{"player_id": str(i)} for i in range(1, 20)]
            s.sync()
            self._check(s)
        finally:
            client.draft_picks = original


class TestStaleStateGuards(unittest.TestCase):
    """A saved board must never silently seed a fresh session.

    Observed live: starting the tool showed 136 picks already made, left over
    from testing. The only guard was the draft id, which matches by definition
    when the leftovers came from testing against the real league. Resuming a
    board the operator did not expect looks like the draft is already underway,
    which is worse than starting empty.
    """

    def setUp(self):
        self.cfg = config.load()
        self.path = pathlib.Path(tempfile.mkdtemp()) / "s.json"

    def _write(self, *, age_hours=0.0, api_base=None, draft_id=None, claims=5):
        from ffopt import client
        import json
        import time
        with open(self.path, "w") as f:
            json.dump({
                "draft_id": draft_id or self.cfg.draft_id,
                "api_base": client.API_V1 if api_base is None else api_base,
                "saved_at": time.time() - age_hours * 3600,
                "mode": "manual", "seat": 1,
                "claims": [[str(i), "manual", 0] for i in range(claims)],
            }, f)

    def _session(self):
        return session.DraftSession(self.cfg, board=_board(), path=self.path)

    def test_recent_board_from_the_same_draft_is_restored(self):
        self._write(age_hours=0.01)
        s = self._session()
        self.assertTrue(s.load())
        self.assertEqual(s.picks_made, 5)

    def test_old_board_is_ignored(self):
        """A draft lasts under an hour; a day-old board is leftover state."""
        self._write(age_hours=9)
        s = self._session()
        self.assertFalse(s.load())
        self.assertEqual(s.picks_made, 0)
        self.assertIn("hours ago", s.stale_state_reason)

    def test_board_saved_against_a_mock_api_is_ignored(self):
        """Rehearsing must not leave picks behind for the real draft."""
        self._write(api_base="http://127.0.0.1:8899/v1")
        s = self._session()
        self.assertFalse(s.load())
        self.assertEqual(s.picks_made, 0)
        self.assertIn("different API", s.stale_state_reason)

    def test_board_from_another_draft_is_ignored(self):
        self._write(draft_id="some-other-draft")
        s = self._session()
        self.assertFalse(s.load())

    def test_saved_board_records_its_provenance(self):
        import json
        from ffopt import client
        s = self._session()
        s.claim("1")
        data = json.loads(self.path.read_text())
        self.assertEqual(data["api_base"], client.API_V1)
        self.assertIn("saved_at", data)


class TestSetup(unittest.TestCase):
    """Seat and mode are asked for, never assumed."""

    def setUp(self):
        self.cfg = config.load()
        self.path = pathlib.Path(tempfile.mkdtemp()) / "s.json"
        self.s = session.DraftSession(self.cfg, board=_board(), path=self.path)

    def test_defaults_to_manual_and_unconfigured(self):
        """Manual is the only mode that cannot be wrong about the board."""
        self.assertEqual(self.s.mode, "manual")
        self.assertFalse(self.s.configured)
        self.assertIsNone(self.s.seat)

    def test_start_sets_seat_and_mode_together(self):
        self.s.start(4, "live")
        self.assertEqual(self.s.seat, 4)
        self.assertEqual(self.s.mode, "live")
        self.assertTrue(self.s.configured)

    def test_start_allows_an_unknown_seat(self):
        """The draft order may not be drawn yet; that is a real answer."""
        self.s.start(None, "manual")
        self.assertIsNone(self.s.seat)
        self.assertTrue(self.s.configured)

    def test_start_rejects_a_bad_seat_or_mode(self):
        with self.assertRaises(session.SessionError):
            self.s.start(99, "manual")
        with self.assertRaises(session.SessionError):
            self.s.start(1, "telepathy")
        self.assertFalse(self.s.configured, "a rejected setup must not stick")

    def test_setup_survives_a_restart(self):
        self.s.start(7, "assisted")
        again = session.DraftSession(self.cfg, board=_board(), path=self.path)
        self.assertTrue(again.load())
        self.assertEqual((again.seat, again.mode, again.configured), (7, "assisted", True))

    def test_go_manual_never_touches_the_board(self):
        """The escape hatch must be safe to hit at any moment."""
        self.s.start(2, "live")
        for pid in list(self.s.available())[:4]:
            self.s.claim(pid.player_id)
        before = list(self.s.claimed_ids)
        self.s.go_manual()
        self.assertEqual(self.s.mode, "manual")
        self.assertEqual(list(self.s.claimed_ids), before)
        self.assertEqual(self.s.seat, 2)

    def test_reset_clears_picks_but_keeps_identity(self):
        """Re-entering who you are with a clock running is a second problem."""
        self.s.start(5, "assisted")
        for pid in list(self.s.available())[:3]:
            self.s.claim(pid.player_id)
        self.s.reset()
        self.assertEqual(self.s.picks_made, 0)
        self.assertEqual(self.s.seat, 5)
        self.assertEqual(self.s.mode, "assisted")

    def test_a_seat_is_never_inferred_from_roster_slots(self):
        """slot_to_roster_id is not a draft order.

        Before the draw it is an identity mapping of slot to roster id. Reading
        a seat out of it would invent an order that does not exist, and the seat
        determines the whole pick schedule.
        """
        self.s._infer_seat([])
        self.assertIsNone(self.s.seat)
        self.assertIsNone(self.s.seat_source)

    def test_a_pick_attributed_to_us_sets_the_seat(self):
        self.s._infer_seat([
            {"picked_by": "someone-else", "draft_slot": 3, "player_id": "1"},
            {"picked_by": self.cfg.my_user_id, "draft_slot": 8, "player_id": "2"},
        ])
        self.assertEqual(self.s.seat, 8)
        self.assertEqual(self.s.seat_source, "picks")

    def test_the_operators_seat_is_never_overridden(self):
        self.s.set_seat(6)
        self.s._infer_seat([
            {"picked_by": self.cfg.my_user_id, "draft_slot": 1, "player_id": "2"},
        ])
        self.assertEqual(self.s.seat, 6)


class TestAdviceDeterminism(unittest.TestCase):
    """The same board must always produce the same advice.

    The rollout is a Monte Carlo estimate. Left unseeded it returned a
    different ranking on each call, and on an opening board -- where the top
    candidates sit within a couple of points of each other -- that was enough
    to swap a 144-value running back for a 43-value quarterback between two
    refreshes with nothing about the draft having changed.
    """

    def setUp(self):
        self.cfg = config.load()
        self.path = pathlib.Path(tempfile.mkdtemp()) / "s.json"
        self.s = session.DraftSession(self.cfg, board=_board(), path=self.path)
        self.s.start(3, "manual")

    def test_repeated_calls_agree(self):
        runs = {
            tuple(r["name"] for r in self.s.recommendations(trials=8, count=4))
            for _ in range(5)
        }
        self.assertEqual(len(runs), 1, f"advice varied between calls: {runs}")

    def test_a_new_board_gets_an_independent_estimate(self):
        """Seeding must not freeze the estimator across different positions."""
        first = self.s._advice_seed(self.s.capture())
        self.s.claim(self.s.available()[0].player_id)
        self.assertNotEqual(first, self.s._advice_seed(self.s.capture()))

    def test_the_seed_ignores_wall_clock_and_mode(self):
        before = self.s._advice_seed(self.s.capture())
        self.s.go_manual()
        self.assertEqual(before, self.s._advice_seed(self.s.capture()))

    def test_the_seed_tracks_the_seat(self):
        """Seat changes the pick schedule, so it must change the estimate."""
        before = self.s._advice_seed(self.s.capture())
        self.s.set_seat(7)
        self.assertNotEqual(before, self.s._advice_seed(self.s.capture()))

    def test_live_trials_exceed_the_measured_convergence_point(self):
        """Seeds agreed from 60 upward; below that the top pick flipped."""
        self.assertGreaterEqual(session.LIVE_TRIALS, 60)


class TestFastEntry(unittest.TestCase):
    """Recording an opponent's pick is the time-critical path.

    Between two of our own turns up to eighteen picks can land, and nine
    autopicking opponents take only seconds. A mock draft was lost on the
    second pick because entry was too slow: the search collapsed to a single
    wrong player and correcting it cost more than the entry saved.
    """

    def setUp(self):
        self.cfg = config.load()
        self.path = pathlib.Path(tempfile.mkdtemp()) / "s.json"
        self.s = session.DraftSession(self.cfg, board=_board(), path=self.path)
        self.s.start(3, "manual")

    def test_suggest_never_hides_alternatives(self):
        """`find` collapses to one plausible name; `suggest` must not.

        The board deliberately reuses surnames across positions, mirroring the
        real pool where "Smith" is one draftable player and several who are
        not. Live, that collapse recorded the wrong player silently.
        """
        results = self.s.suggest("ashford", limit=8)
        self.assertGreater(len(results), 1, f"only got {results}")
        self.assertTrue(
            all("ashford" in r["name"].lower() for r in results), results
        )

    def test_find_still_collapses_where_suggest_does_not(self):
        """The two must genuinely differ, or nothing was actually fixed.

        Reproduces the real shape of the problem: one draftable player shares a
        surname with several who will never be claimed. `find` treats that as
        unambiguous and returns the one; the operator who meant a different one
        gets no say. `suggest` shows them all.
        """
        board = [
            _item("s1", "DeVonta Smith", "WR", 32.3),
            _item("s2", "Brashard Smith", "RB", 226.3),
            _item("s3", "Terrelle Smith", "RB", 347.5),
            _item("s4", "Arian Smith", "WR", 438.4),
        ]
        sess = session.DraftSession(self.cfg, board=board, path=self.path)
        self.assertEqual(len(sess.search("smith")), 1)
        self.assertEqual(sess.search("smith")[0]["name"], "DeVonta Smith")

        expanded = sess.suggest("smith", limit=8)
        self.assertEqual(len(expanded), 4)
        self.assertEqual(expanded[0]["name"], "DeVonta Smith")

    def test_suggest_puts_the_likeliest_player_first(self):
        """Ordering is by market consensus, so the likely one is on top."""
        results = self.s.suggest("ashford", limit=8)
        adps = [r["adp"] for r in results if r["adp"] is not None]
        self.assertEqual(adps, sorted(adps))

    def test_suggest_matches_a_partial_surname(self):
        results = self.s.suggest("ashf", limit=8)
        self.assertTrue(results)
        self.assertTrue(all("Ashford" in r["name"] for r in results), results)

    def test_suggest_is_empty_for_nonsense(self):
        self.assertEqual(self.s.suggest("zzzzqqqq", limit=8), [])

    def test_suggest_respects_the_limit(self):
        self.assertLessEqual(len(self.s.suggest("a", limit=5)), 5)

    def test_suggest_skips_players_already_taken(self):
        first = self.s.suggest("ashford", limit=8)[0]
        self.s.claim(first["player_id"])
        again = [r["player_id"] for r in self.s.suggest("ashford", limit=8)]
        self.assertNotIn(first["player_id"], again)

    def test_quick_board_is_market_ordered(self):
        quick = self.s.quick_board(limit=10)
        self.assertEqual(len(quick), 10)
        adps = [q["adp"] for q in quick]
        self.assertEqual(adps, sorted(adps))

    def test_quick_board_drops_claimed_players(self):
        first = self.s.quick_board(limit=5)[0]
        self.s.claim(first["player_id"])
        after = [q["player_id"] for q in self.s.quick_board(limit=5)]
        self.assertNotIn(first["player_id"], after)
        self.assertEqual(len(after), 5, "the grid must refill, not shrink")

    def test_quick_board_survives_a_full_burst(self):
        """Eighteen picks back to back, the worst realistic gap."""
        seen = set()
        for _ in range(18):
            pid = self.s.quick_board(limit=18)[0]["player_id"]
            self.assertNotIn(pid, seen, "a claimed player was offered again")
            self.s.claim(pid)
            seen.add(pid)
        self.assertEqual(self.s.picks_made, 18)

    def test_snapshot_carries_the_grid(self):
        """The grid must arrive with state, not cost an extra round trip."""
        self.assertTrue(self.s.snapshot()["quick"])


class TestShortNames(unittest.TestCase):
    """Display names match the platform's own format.

    During a draft the operator compares this screen against the draft room.
    Two differently formatted names take measurably longer to match than two
    identical ones, and that time comes out of a 60-second budget.
    """

    def setUp(self):
        self.cfg = config.load()
        self.path = pathlib.Path(tempfile.mkdtemp()) / "s.json"

    def test_a_normal_name_is_abbreviated(self):
        self.assertEqual(pool.short_name("Jahmyr Gibbs", "RB"), "J. Gibbs")

    def test_punctuation_in_a_first_name_is_handled(self):
        self.assertEqual(pool.short_name("Ja'Marr Chase", "WR"), "J. Chase")

    def test_a_multi_token_surname_is_kept_whole(self):
        self.assertEqual(pool.short_name("Amon-Ra St. Brown", "WR"), "A. St. Brown")
        self.assertEqual(
            pool.short_name("Jaxon Smith-Njigba", "WR"), "J. Smith-Njigba"
        )

    def test_an_existing_initialism_is_left_alone(self):
        """"A. Brown" is no shorter, and there are several Browns."""
        self.assertEqual(pool.short_name("A.J. Brown", "WR"), "A.J. Brown")
        self.assertEqual(pool.short_name("T.J. Hockenson", "TE"), "T.J. Hockenson")

    def test_a_defense_shows_its_nickname(self):
        """Team defenses have no personal name; the nickname identifies them."""
        self.assertEqual(pool.short_name("Los Angeles Rams", "DEF"), "Rams")
        self.assertEqual(pool.short_name("San Francisco 49ers", "DEF"), "49ers")

    def test_a_single_word_name_is_unchanged(self):
        self.assertEqual(pool.short_name("Cher", "WR"), "Cher")
        self.assertEqual(pool.short_name("", "WR"), "")

    def test_an_ambiguous_name_keeps_its_full_form(self):
        """Bijan and Brian Robinson are both draftable running backs.

        Collapsing both to "B. Robinson" would make recording the wrong one a
        coin flip -- precisely the error the format is meant to prevent.
        """
        board = [
            _item("r1", "Bijan Robinson", "RB", 2.2),
            _item("r2", "Brian Robinson", "RB", 118.0),
            _item("g1", "Jahmyr Gibbs", "RB", 1.2),
        ]
        s = session.DraftSession(self.cfg, board=board, path=self.path)
        by_name = {b["name"]: b["short"] for b in s.quick_board(limit=5)}
        self.assertEqual(by_name["Bijan Robinson"], "Bijan Robinson")
        self.assertEqual(by_name["Brian Robinson"], "Brian Robinson")
        self.assertEqual(by_name["Jahmyr Gibbs"], "J. Gibbs")

    def test_an_undraftable_namesake_does_not_spoil_the_short_form(self):
        """Judging ambiguity over the whole pool would cost a quarter of them."""
        board = [
            _item("s1", "DeVonta Smith", "WR", 32.3),
            _item("s2", "Terrelle Smith", "RB", 347.5),
        ]
        s = session.DraftSession(self.cfg, board=board, path=self.path)
        shorts = {b["name"]: b["short"] for b in s.quick_board(limit=5)}
        self.assertEqual(shorts["DeVonta Smith"], "D. Smith")

    def test_every_brief_carries_a_short_name(self):
        s = session.DraftSession(self.cfg, board=_board(), path=self.path)
        s.start(3, "manual")
        for entry in s.quick_board(limit=6) + s.suggest("ash", limit=4):
            self.assertTrue(entry["short"], entry)
        s.claim(s.quick_board(limit=1)[0]["player_id"])
        self.assertTrue(s.snapshot()["recent"][0]["short"])


class TestAlignment(unittest.TestCase):
    """Detecting a board that has drifted out of step with the room.

    The worst failure in a live draft, because it is silent. A missed or
    duplicated entry shifts every later pick by one; the board still looks
    orderly, but the tool now believes players are available who are gone and
    attributes picks to the wrong rosters. It surfaced in a rehearsal only when
    the operator's own turn arrived, several picks of bad advice later.
    """

    def setUp(self):
        self.cfg = config.load()
        self.path = pathlib.Path(tempfile.mkdtemp()) / "s.json"
        self.s = session.DraftSession(self.cfg, board=_board(), path=self.path)
        self.s.start(5, "manual")
        self.ids = [i.player_id for i in self.s.available()[:8]]
        self._original = client.draft_picks

    def tearDown(self):
        client.draft_picks = self._original

    def _feed(self, ids):
        client.draft_picks = lambda _d: [{"player_id": p} for p in ids]

    def test_a_matching_board_is_aligned(self):
        self._feed(self.ids[:5])
        for pid in self.ids[:5]:
            self.s.claim(pid)
        a = self.s.alignment()
        self.assertTrue(a["aligned"])
        self.assertEqual(a["drift"], 0)
        self.assertIsNone(a["first_conflict"])

    def test_a_missed_pick_is_detected(self):
        """The mistake actually made: one opponent pick never recorded."""
        self._feed(self.ids[:5])
        for pid in self.ids[:4]:
            self.s.claim(pid)
        a = self.s.alignment()
        self.assertFalse(a["aligned"])
        self.assertEqual(a["drift"], -1)
        self.assertEqual(a["local_label"], self.cfg.pick_label(5))
        self.assertEqual(a["remote_label"], self.cfg.pick_label(6))

    def test_a_duplicated_pick_is_detected(self):
        self._feed(self.ids[:3])
        for pid in self.ids[:5]:
            self.s.claim(pid)
        self.assertEqual(self.s.alignment()["drift"], 2)

    def test_a_wrong_player_is_detected_even_when_the_count_matches(self):
        """The harder case: nothing about the totals looks wrong."""
        self._feed(self.ids[:5])
        for pid in self.ids[:5]:
            self.s.claim(pid)
        self.s.correct(2, self.ids[6])
        a = self.s.alignment()
        self.assertFalse(a["aligned"])
        self.assertEqual(a["drift"], 0, "counts agree; only the content differs")
        self.assertEqual(a["first_conflict"], 2)
        self.assertEqual(a["first_conflict_label"], self.cfg.pick_label(2))

    def test_being_offline_is_reported_not_guessed(self):
        """Manual mode must keep working with no network at all."""
        def boom(_d):
            raise RuntimeError("no network")
        client.draft_picks = boom
        a = self.s.alignment()
        self.assertFalse(a["checked"])
        self.assertIsNone(a["aligned"])
        self.assertIn("no network", a["error"])

    def test_the_check_never_changes_the_board(self):
        """Manual mode exists so the feed cannot rewrite the operator's board."""
        self._feed(self.ids[:6])
        self.s.claim(self.ids[0])
        before = list(self.s.claimed_ids)
        self.s.alignment()
        self.assertEqual(list(self.s.claimed_ids), before)
        self.assertEqual(self.s.picks_made, 1)

    def test_adopting_the_feed_repairs_a_drifted_board(self):
        self._feed(self.ids[:5])
        for pid in self.ids[:3]:
            self.s.claim(pid)
        result = self.s.adopt_feed()
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed"], 2)
        self.assertTrue(self.s.alignment()["aligned"])

    def test_adopting_discards_entries_past_the_feed(self):
        """Entries beyond the feed are what a mis-entry looks like.

        `sync` keeps them on purpose, because assisted mode runs ahead of a
        lagging feed. Recovery must not, or the drift survives the repair.
        """
        self._feed(self.ids[:3])
        for pid in self.ids[:6]:
            self.s.claim(pid)
        self.s.adopt_feed()
        self.assertEqual(self.s.picks_made, 3)
        self.assertTrue(self.s.alignment()["aligned"])

    def test_adopting_offline_fails_without_destroying_the_board(self):
        def boom(_d):
            raise RuntimeError("offline")
        client.draft_picks = boom
        for pid in self.ids[:4]:
            self.s.claim(pid)
        result = self.s.adopt_feed()
        self.assertFalse(result["ok"])
        self.assertEqual(self.s.picks_made, 4, "a failed repair must not clear picks")


class TestPickLabels(unittest.TestCase):
    """Pick numbering must match the platform's, or it cannot be compared."""

    def setUp(self):
        self.cfg = config.load()

    def test_labels_match_the_platform_format(self):
        n = self.cfg.num_agents
        self.assertEqual(self.cfg.pick_label(1), "1.1")
        self.assertEqual(self.cfg.pick_label(n), f"1.{n}")
        self.assertEqual(self.cfg.pick_label(n + 1), "2.1")

    def test_labels_count_within_the_round_not_by_seat(self):
        """Even rounds run backwards: round 2 position 1 is the last seat."""
        n = self.cfg.num_agents
        self.assertEqual(self.cfg.seat_of_pick(n + 1), n)
        self.assertEqual(self.cfg.pick_label(n + 1), "2.1")

    def test_a_seats_labels_snake(self):
        """Verified against a real draft board screenshot for seat 5."""
        if self.cfg.num_agents != 10:
            self.skipTest("fixture is for a 10-team league")
        labels = [self.cfg.pick_label(n) for n in self.cfg.pick_numbers(5)][:6]
        self.assertEqual(labels, ["1.5", "2.6", "3.5", "4.6", "5.5", "6.6"])

    def test_the_snapshot_exposes_the_label(self):
        s = session.DraftSession(self.cfg, board=_board(),
                                 path=pathlib.Path(tempfile.mkdtemp()) / "s.json")
        s.start(5, "manual")
        self.assertEqual(s.snapshot()["pick_label"], "1.1")
        s.claim(s.available()[0].player_id)
        self.assertEqual(s.snapshot()["pick_label"], "1.2")
