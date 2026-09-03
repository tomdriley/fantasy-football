"""Tests for the HTTP interface.

These drive a real server over real sockets rather than calling handlers
directly, because the failure modes that matter on draft day -- a malformed
body, a dead endpoint, two requests racing -- live in the transport layer as
much as in the logic.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from ffopt import client, pool, session, webapp


def _item(pid, name, pos, adp, payoff=200.0):
    return pool.Item(player_id=pid, name=name, pos=pos, team="TM",
                     payoff=payoff, adp=adp, games=17.0)


_NAMES = [
    "Ashford", "Brennan", "Calloway", "Danforth", "Ellsworth", "Fairbank",
    "Garrity", "Halloran", "Ingersoll", "Jessup", "Kirkland", "Lamonte",
    "Merriweather", "Northcott", "Ollivander", "Prescott", "Quimby",
    "Ravensworth", "Sutherland", "Thorncastle",
]


def _board():
    items, n = [], 1
    for pos, count, base in (("RB", 40, 300.0), ("WR", 40, 290.0), ("TE", 40, 200.0),
                             ("QB", 40, 320.0), ("K", 20, 80.0), ("DEF", 20, 110.0)):
        for i in range(count):
            items.append(_item(str(n), f"{_NAMES[i % len(_NAMES)]}{pos}{i}", pos,
                               float(n), base - i * 4))
            n += 1
    return items


class WebTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "session.json"
        sess = session.DraftSession(board=_board(), path=tmp)
        cls.service = webapp.DraftService(sess)
        cls.server = webapp.serve(0, cls.service)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.service.session.reset()
        self.service.session.set_seat(None)
        self.service.session.set_mode("live")
        self.service.session.configured = False
        self.service.invalidate()

    # -- helpers --------------------------------------------------------
    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        try:
            with urllib.request.urlopen(self.url(path), timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def post(self, path, payload=None, raw=None):
        data = raw if raw is not None else json.dumps(payload or {}).encode()
        req = urllib.request.Request(
            self.url(path), data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)


class TestStaticAndRouting(WebTestCase):
    def test_index_is_served(self):
        with urllib.request.urlopen(self.url("/"), timeout=30) as r:
            body = r.read().decode()
        self.assertEqual(r.status, 200)
        self.assertIn("Git Blame Copilot", body)

    def test_assets_are_served(self):
        for asset, needle in (("/app.js", "panic"), ("/style.css", "--accent")):
            with urllib.request.urlopen(self.url(asset), timeout=30) as r:
                self.assertEqual(r.status, 200)
                self.assertIn(needle, r.read().decode())

    def test_unknown_api_endpoint_is_404(self):
        status, body = self.get("/api/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_directory_traversal_is_refused(self):
        for path in ("/../ffopt/config.py", "/../../etc/passwd"):
            try:
                with urllib.request.urlopen(self.url(path), timeout=30) as r:
                    self.assertNotIn("SECRET", r.read().decode())
                    self.assertNotEqual(r.status, 200)
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 404)


class TestState(WebTestCase):
    def test_state_shape(self):
        status, body = self.get("/api/state")
        self.assertEqual(status, 200)
        for key in ("mode", "seat", "picks_made", "total_picks", "current_pick",
                    "my_turn", "complete", "roster", "unfilled", "recent"):
            self.assertIn(key, body)

    def test_set_seat_and_mode(self):
        _, body = self.post("/api/seat", {"seat": 4})
        self.assertEqual(body["state"]["seat"], 4)
        _, body = self.post("/api/mode", {"mode": "manual"})
        self.assertEqual(body["state"]["mode"], "manual")

    def test_invalid_seat_returns_400(self):
        status, body = self.post("/api/seat", {"seat": 99})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_mode_returns_400(self):
        status, _ = self.post("/api/mode", {"mode": "wizardry"})
        self.assertEqual(status, 400)


class TestClaiming(WebTestCase):
    def test_claim_by_query(self):
        status, body = self.post("/api/claim", {"query": "AshfordRB0"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"]["picks_made"], 1)

    def test_claim_by_id(self):
        _, body = self.post("/api/claim", {"player_id": "1"})
        self.assertTrue(body["ok"])

    def test_duplicate_reports_already_taken(self):
        self.post("/api/claim", {"player_id": "1"})
        status, body = self.post("/api/claim", {"player_id": "1"})
        self.assertEqual(status, 400)
        self.assertIn("already", body["error"])

    def test_ambiguous_query_returns_options(self):
        status, body = self.post("/api/claim", {"query": "Ashford"})
        self.assertFalse(body["ok"])
        self.assertTrue(body["ambiguous"])
        self.assertGreater(len(body["results"]), 1)
        self.assertEqual(body["state"]["picks_made"], 0, "must not claim on ambiguity")

    def test_unknown_query_reports_no_match(self):
        _, body = self.post("/api/claim", {"query": "zzzznotaplayer"})
        self.assertFalse(body["ok"])
        self.assertIn("no match", body["error"])

    def test_claim_without_arguments_is_rejected(self):
        status, body = self.post("/api/claim", {})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_malformed_json_does_not_crash(self):
        status, body = self.post("/api/claim", raw=b"{not json at all")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_empty_body_does_not_crash(self):
        status, _ = self.post("/api/claim", raw=b"")
        self.assertEqual(status, 400)


class TestCorrections(WebTestCase):
    def setUp(self):
        super().setUp()
        for pid in ("1", "2", "3"):
            self.post("/api/claim", {"player_id": pid})

    def test_undo(self):
        _, body = self.post("/api/undo")
        self.assertEqual(body["state"]["picks_made"], 2)

    def test_correct(self):
        _, body = self.post("/api/correct", {"pick": 2, "player_id": "50"})
        self.assertTrue(body["ok"])
        recent = {r["pick"]: r["player_id"] for r in body["state"]["recent"]}
        self.assertEqual(recent[2], "50")

    def test_insert_shifts_and_changes_ownership(self):
        self.post("/api/seat", {"seat": 3})
        _, before = self.get("/api/state")
        self.assertEqual([p["player_id"] for p in before["roster"]], ["3"])
        _, body = self.post("/api/insert", {"pick": 1, "player_id": "60"})
        self.assertEqual([p["player_id"] for p in body["state"]["roster"]], ["2"])

    def test_remove(self):
        _, body = self.post("/api/remove", {"pick": 1})
        self.assertEqual(body["state"]["picks_made"], 2)

    def test_out_of_range_pick_returns_400(self):
        status, body = self.post("/api/correct", {"pick": 99, "player_id": "50"})
        self.assertEqual(status, 400)
        self.assertIn("state", body, "the client still needs current state on error")

    def test_missing_field_returns_400(self):
        status, _ = self.post("/api/correct", {"pick": 1})
        self.assertEqual(status, 400)

    def test_reset(self):
        _, body = self.post("/api/reset")
        self.assertEqual(body["state"]["picks_made"], 0)


class TestAdviceEndpoints(WebTestCase):
    def test_panic_is_fast_and_never_empty(self):
        import time
        self.post("/api/seat", {"seat": 5})
        start = time.perf_counter()
        status, body = self.get("/api/panic")
        elapsed = time.perf_counter() - start
        self.assertEqual(status, 200)
        self.assertTrue(body["picks"])
        self.assertLess(elapsed, 2.0)

    def test_panic_works_with_no_seat_set(self):
        status, body = self.get("/api/panic")
        self.assertEqual(status, 200)
        self.assertTrue(body["picks"])

    def test_recommend_returns_picks(self):
        self.post("/api/seat", {"seat": 5})
        status, body = self.get("/api/recommend?trials=2")
        self.assertEqual(status, 200)
        self.assertTrue(body["picks"])

    def test_recommend_is_cached_between_calls(self):
        self.post("/api/seat", {"seat": 5})
        _, first = self.get("/api/recommend?trials=2")
        _, second = self.get("/api/recommend?trials=2")
        self.assertEqual([p["player_id"] for p in first["picks"]],
                         [p["player_id"] for p in second["picks"]])

    def test_cache_is_invalidated_by_a_claim(self):
        self.post("/api/seat", {"seat": 5})
        _, first = self.get("/api/recommend?trials=2")
        top = first["picks"][0]["player_id"]
        self.post("/api/claim", {"player_id": top})
        _, second = self.get("/api/recommend?trials=2")
        self.assertNotIn(top, [p["player_id"] for p in second["picks"]],
                         "a claimed player must not still be recommended")

    def test_absurd_trial_count_is_clamped(self):
        self.post("/api/seat", {"seat": 5})
        status, _ = self.get("/api/recommend?trials=999999")
        self.assertEqual(status, 200)

    def test_search_endpoint(self):
        status, body = self.get("/api/search?q=AshfordRB0")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["results"]), 1)

    def test_search_with_empty_query(self):
        status, body = self.get("/api/search?q=")
        self.assertEqual(status, 200)
        self.assertEqual(body["results"], [])


class TestConcurrency(WebTestCase):
    def test_parallel_claims_do_not_corrupt_the_board(self):
        """Two clients (or a double-click) must not produce a duplicate."""
        results = []

        def claim():
            results.append(self.post("/api/claim", {"player_id": "1"}))

        threads = [threading.Thread(target=claim) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r[1].get("ok")]
        self.assertEqual(len(successes), 1, "exactly one claim may succeed")
        _, state = self.get("/api/state")
        self.assertEqual(state["picks_made"], 1)

    def test_reads_during_writes_stay_consistent(self):
        errors = []

        def reader():
            for _ in range(20):
                try:
                    status, body = self.get("/api/state")
                    if status != 200 or "picks_made" not in body:
                        errors.append(body)
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))

        def writer():
            for pid in range(1, 15):
                self.post("/api/claim", {"player_id": str(pid)})

        threads = [threading.Thread(target=reader), threading.Thread(target=writer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class TestSyncDegradation(WebTestCase):
    def test_sync_failure_returns_a_usable_response(self):
        from ffopt import client
        original = client.draft_picks
        client.draft_picks = lambda _d: (_ for _ in ()).throw(OSError("no network"))
        try:
            status, body = self.post("/api/sync")
        finally:
            client.draft_picks = original
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("state", body, "the interface must keep working offline")

    def test_claims_survive_a_failed_sync(self):
        from ffopt import client
        self.post("/api/claim", {"player_id": "1"})
        original = client.draft_picks
        client.draft_picks = lambda _d: (_ for _ in ()).throw(OSError("no network"))
        try:
            self.post("/api/sync")
        finally:
            client.draft_picks = original
        _, state = self.get("/api/state")
        self.assertEqual(state["picks_made"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestProposeEndpoint(WebTestCase):
    def test_propose_does_not_mutate(self):
        from ffopt import client
        original = client.draft_picks
        client.draft_picks = lambda _d: [{"player_id": "1"}, {"player_id": "2"}]
        try:
            status, body = self.get("/api/propose")
        finally:
            client.draft_picks = original
        self.assertEqual(status, 200)
        self.assertEqual(len(body["additions"]), 2)
        self.assertEqual(body["state"]["picks_made"], 0)

    def test_propose_handles_a_dead_feed(self):
        from ffopt import client
        original = client.draft_picks
        client.draft_picks = lambda _d: (_ for _ in ()).throw(OSError("down"))
        try:
            status, body = self.get("/api/propose")
        finally:
            client.draft_picks = original
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("state", body)


class TestLockContention(WebTestCase):
    """A running simulation must not block the operator.

    Recommendations take seconds on a full board. If that work is done while
    holding the service lock, the operator cannot record a pick or hit panic
    until it finishes -- and panic exists precisely for the moment the clock is
    running out.
    """

    def test_panic_is_not_blocked_by_a_running_recommendation(self):
        import threading
        import time

        self.post("/api/seat", {"seat": 5})
        timings = {}

        def slow():
            self.service.recommend(trials=30)

        def panic():
            time.sleep(0.15)
            start = time.perf_counter()
            self.service.panic()
            timings["panic"] = time.perf_counter() - start

        def claim():
            time.sleep(0.2)
            start = time.perf_counter()
            self.service.claim("1", None)
            timings["claim"] = time.perf_counter() - start

        threads = [threading.Thread(target=f) for f in (slow, panic, claim)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertLess(timings["panic"], 1.0,
                        f"panic blocked for {timings['panic']:.2f}s")
        self.assertLess(timings["claim"], 1.0,
                        f"claim blocked for {timings['claim']:.2f}s")

    def test_result_is_not_cached_if_the_board_moved(self):
        """A recommendation computed against a stale board must not be stored."""
        self.post("/api/seat", {"seat": 5})
        snapshot = self.service.session.capture()
        self.service.claim("1", None)
        picks = self.service.session.recommendations(trials=2, snapshot=snapshot)
        self.assertTrue(picks)
        fresh = self.service.recommend(trials=2)
        self.assertNotIn("1", [p["player_id"] for p in fresh["picks"]])


class TestResponsiveness(WebTestCase):
    """Latency guards.

    The interface was measurably sluggish: a recommendation after a claim took
    nearly nine seconds because the rollout rescanned every remaining player on
    every simulated pick. Two exact optimisations removed that. These tests fail
    if the hot paths regress, since a slow tool is a tool that costs picks.
    """

    def test_recommendation_is_fast_on_a_full_board(self):
        import time
        self.post("/api/seat", {"seat": 5})
        self.service.invalidate()
        start = time.perf_counter()
        self.service.recommend(trials=30)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 5.0, f"recommendation took {elapsed:.1f}s")

    def test_recommendation_stays_fast_mid_draft(self):
        import time
        self.post("/api/seat", {"seat": 5})
        for pid in range(1, 40):
            self.service.claim(str(pid), None)
        self.service.invalidate()
        start = time.perf_counter()
        self.service.recommend(trials=30)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 5.0, f"mid-draft recommendation took {elapsed:.1f}s")

    def test_panic_is_effectively_instant(self):
        import time
        self.post("/api/seat", {"seat": 5})
        start = time.perf_counter()
        self.service.panic()
        self.assertLess(time.perf_counter() - start, 0.5)

    def test_state_read_is_effectively_instant(self):
        import time
        start = time.perf_counter()
        for _ in range(20):
            self.service.state()
        self.assertLess(time.perf_counter() - start, 1.0)


class TestFreshStart(unittest.TestCase):
    """The operator must be able to force an empty board."""

    def test_fresh_discards_a_saved_board(self):
        import json
        import time
        from ffopt import client, config, session
        cfg = config.load()
        path = pathlib.Path(tempfile.mkdtemp()) / "s.json"
        with open(path, "w") as f:
            json.dump({
                "draft_id": cfg.draft_id, "api_base": client.API_V1,
                "saved_at": time.time(), "mode": "manual", "seat": 3,
                "claims": [["1", "manual", 0], ["2", "manual", 0]],
            }, f)
        restored = session.DraftSession(cfg, board=_board(), path=path)
        self.assertTrue(restored.load())
        self.assertEqual(restored.picks_made, 2)
        restored.reset()
        self.assertEqual(restored.picks_made, 0)
        reloaded = session.DraftSession(cfg, board=_board(), path=path)
        reloaded.load()
        self.assertEqual(reloaded.picks_made, 0, "reset must persist")


class TestSetupEndpoints(WebTestCase):
    """The setup screen and the manual escape hatch, over HTTP."""

    def test_start_sets_seat_and_mode_in_one_call(self):
        code, body = self.post("/api/start", {"seat": 4, "mode": "assisted"})
        self.assertEqual(code, 200)
        self.assertEqual(body["state"]["seat"], 4)
        self.assertEqual(body["state"]["mode"], "assisted")
        self.assertTrue(body["state"]["configured"])

    def test_start_accepts_an_undrawn_order(self):
        code, body = self.post("/api/start", {"seat": None, "mode": "manual"})
        self.assertEqual(code, 200)
        self.assertIsNone(body["state"]["seat"])
        self.assertTrue(body["state"]["configured"])

    def test_start_rejects_a_bad_seat(self):
        code, _ = self.post("/api/start", {"seat": 99, "mode": "manual"})
        self.assertEqual(code, 400)
        self.assertFalse(self.service.session.configured)

    def test_manual_endpoint_preserves_the_board(self):
        self.post("/api/start", {"seat": 2, "mode": "live"})
        ids = [i.player_id for i in self.service.session.available()[:3]]
        for pid in ids:
            self.post("/api/claim", {"player_id": pid})
        code, body = self.post("/api/manual")
        self.assertEqual(code, 200)
        self.assertEqual(body["state"]["mode"], "manual")
        self.assertEqual(body["state"]["picks_made"], 3)
        self.assertEqual(body["state"]["seat"], 2)

    def test_reset_keeps_seat_and_mode(self):
        self.post("/api/start", {"seat": 6, "mode": "assisted"})
        pid = self.service.session.available()[0].player_id
        self.post("/api/claim", {"player_id": pid})
        code, body = self.post("/api/reset")
        self.assertEqual(code, 200)
        self.assertEqual(body["state"]["picks_made"], 0)
        self.assertEqual(body["state"]["seat"], 6)
        self.assertEqual(body["state"]["mode"], "assisted")

    def test_advice_is_labelled_degraded_without_a_seat(self):
        """Advice without a seat is best-available, not a plan; say so."""
        self.post("/api/start", {"seat": None, "mode": "manual"})
        _, body = self.get("/api/recommend?trials=1")
        self.assertTrue(body["degraded"])
        self.assertTrue(body["picks"])

    def test_advice_is_not_degraded_once_a_seat_is_known(self):
        self.post("/api/start", {"seat": 3, "mode": "manual"})
        _, body = self.get("/api/recommend?trials=1")
        self.assertFalse(body["degraded"])

    def test_advice_recomputes_when_the_seat_arrives(self):
        """The cache must not serve seat-blind advice after the draw."""
        self.post("/api/start", {"seat": None, "mode": "manual"})
        _, blind = self.get("/api/recommend?trials=1")
        self.post("/api/seat", {"seat": 1})
        _, seated = self.get("/api/recommend?trials=1")
        self.assertFalse(seated["degraded"])
        self.assertFalse(seated["cached"])


class TestFastEntryEndpoints(WebTestCase):
    """The click-to-record path, over HTTP."""

    def setUp(self):
        super().setUp()
        self.post("/api/start", {"seat": 3, "mode": "manual"})

    def test_suggest_returns_alternatives(self):
        _, body = self.get("/api/suggest?q=ashford&limit=8")
        self.assertGreater(len(body["results"]), 1)

    def test_suggest_caps_the_limit(self):
        _, body = self.get("/api/suggest?q=a&limit=999")
        self.assertLessEqual(len(body["results"]), 25)

    def test_suggest_handles_an_empty_query(self):
        code, body = self.get("/api/suggest?q=")
        self.assertEqual(code, 200)
        self.assertEqual(body["results"], [])

    def test_state_carries_the_quick_grid(self):
        _, body = self.get("/api/state")
        self.assertTrue(body["quick"])
        self.assertIn("player_id", body["quick"][0])

    def test_a_burst_of_eighteen_picks_stays_consistent(self):
        """The worst realistic gap between two of our own turns."""
        _, state = self.get("/api/state")
        seen = []
        for _ in range(18):
            pid = state["quick"][0]["player_id"]
            self.assertNotIn(pid, seen, "a claimed player was offered again")
            _, body = self.post("/api/claim", {"player_id": pid})
            self.assertTrue(body["ok"])
            seen.append(pid)
            state = body["state"]
        self.assertEqual(state["picks_made"], 18)
        self.assertEqual(len(set(seen)), 18)

    def test_claiming_the_same_player_twice_is_refused_clearly(self):
        """A double click must not silently record two picks."""
        _, state = self.get("/api/state")
        pid = state["quick"][0]["player_id"]
        _, first = self.post("/api/claim", {"player_id": pid})
        self.assertTrue(first["ok"])
        code, second = self.post("/api/claim", {"player_id": pid})
        self.assertFalse(second["ok"])
        self.assertIn("already claimed", second["error"])
        self.assertEqual(second["state"]["picks_made"], 1)


class TestAlignmentEndpoints(WebTestCase):
    """Drift detection and repair, over HTTP."""

    def setUp(self):
        super().setUp()
        self.post("/api/start", {"seat": 5, "mode": "manual"})
        self.ids = [i.player_id for i in self.service.session.available()[:8]]
        self._original = client.draft_picks

    def tearDown(self):
        client.draft_picks = self._original

    def _feed(self, ids):
        client.draft_picks = lambda _d: [{"player_id": p} for p in ids]

    def test_alignment_reports_a_matching_board(self):
        self._feed(self.ids[:4])
        for pid in self.ids[:4]:
            self.post("/api/claim", {"player_id": pid})
        _, body = self.get("/api/alignment")
        self.assertTrue(body["aligned"])

    def test_alignment_reports_a_missed_pick(self):
        self._feed(self.ids[:5])
        for pid in self.ids[:3]:
            self.post("/api/claim", {"player_id": pid})
        _, body = self.get("/api/alignment")
        self.assertFalse(body["aligned"])
        self.assertEqual(body["drift"], -2)

    def test_alignment_does_not_mutate_the_board(self):
        self._feed(self.ids[:6])
        self.post("/api/claim", {"player_id": self.ids[0]})
        self.get("/api/alignment")
        _, state = self.get("/api/state")
        self.assertEqual(state["picks_made"], 1)

    def test_adopt_repairs_the_board(self):
        self._feed(self.ids[:5])
        for pid in self.ids[:2]:
            self.post("/api/claim", {"player_id": pid})
        _, body = self.post("/api/adopt")
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"]["picks_made"], 5)
        _, check = self.get("/api/alignment")
        self.assertTrue(check["aligned"])

    def test_state_carries_the_platform_pick_label(self):
        _, body = self.get("/api/state")
        self.assertEqual(body["pick_label"], "1.1")
