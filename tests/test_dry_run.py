"""Full-draft dry run through the public interface.

The unit tests exercise pieces. This drives a complete 150-pick draft through
the same service the browser talks to, which is the closest thing to a
rehearsal available before the real thing. It asserts the outcome a real draft
must satisfy: a full, legal roster, correct seat attribution throughout, and
advice available at every one of our turns.
"""

from __future__ import annotations

import os
import pathlib
import random
import tempfile
import unittest

from ffopt import config, session, webapp


@unittest.skipUnless(
    os.environ.get("FFOPT_DRYRUN") == "1",
    "slow full-draft rehearsal; run with FFOPT_DRYRUN=1",
)
class TestFullDraftDryRun(unittest.TestCase):
    def test_complete_draft_through_the_service(self):
        cfg = config.load()
        sess = session.DraftSession(
            cfg, path=pathlib.Path(tempfile.mkdtemp()) / "dry.json"
        )
        sess.load_board()
        service = webapp.DraftService(sess)
        service.session.set_mode("manual")
        service.session.set_seat(5)

        rng = random.Random(11)
        my_picks = set(cfg.pick_numbers(5))
        advice_given = 0

        for pick_no in range(1, cfg.rounds * cfg.num_agents + 1):
            state = service.state()
            self.assertEqual(state["current_pick"], pick_no)
            self.assertEqual(state["seat_on_clock"], cfg.seat_of_pick(pick_no))

            if pick_no in my_picks:
                self.assertTrue(state["my_turn"], f"pick {pick_no} should be ours")
                picks = service.recommend(trials=2)["picks"]
                self.assertTrue(picks, f"no advice at pick {pick_no}")
                advice_given += 1
                chosen = picks[0]["player_id"]
            else:
                available = service.session.available()
                chosen = available[min(int(abs(rng.gauss(0, 4))),
                                       len(available) - 1)].player_id

            result = service.claim(chosen, None)
            self.assertTrue(result.get("ok"), result.get("error"))

        final = service.state()
        self.assertTrue(final["complete"])
        self.assertEqual(final["picks_made"], cfg.rounds * cfg.num_agents)
        self.assertEqual(advice_given, cfg.rounds)
        self.assertEqual(len(final["roster"]), cfg.rounds)
        self.assertEqual(final["unfilled"], {},
                         f"roster cannot field: {final['unfilled']}")

        counts: dict[str, int] = {}
        for player in final["roster"]:
            counts[player["pos"]] = counts.get(player["pos"], 0) + 1
        self.assertLessEqual(counts.get("QB", 0), 2, counts)
        self.assertLessEqual(counts.get("K", 0), 1, counts)
        self.assertLessEqual(counts.get("DEF", 0), 1, counts)
        self.assertGreaterEqual(
            sum(counts.get(p, 0) for p in cfg.flex_types), 9, counts
        )

    def test_panic_is_available_at_every_pick(self):
        cfg = config.load()
        sess = session.DraftSession(
            cfg, path=pathlib.Path(tempfile.mkdtemp()) / "dry2.json"
        )
        sess.load_board()
        service = webapp.DraftService(sess)
        service.session.set_seat(1)
        rng = random.Random(3)
        for _ in range(cfg.rounds * cfg.num_agents):
            picks = service.panic()["picks"]
            self.assertTrue(picks, "panic must never come back empty")
            available = service.session.available()
            service.claim(
                available[min(int(abs(rng.gauss(0, 4))), len(available) - 1)].player_id,
                None,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
