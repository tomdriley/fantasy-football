"""Golden-file regression guard on the validated draft behaviour.

The engine's measured advantage (+9.0% over the platform default across five
seasons) rests on the exact sequence of picks it makes. Any change that alters
that sequence invalidates the measurement until it is re-run.

These tests replay a fixed set of drafts and compare against a captured
snapshot. They are deliberately strict: a diff here does not necessarily mean
the change is *wrong*, but it does mean the backtest must be re-run before the
documented result can still be claimed.

Regenerate deliberately, never casually:
    python3 scripts/capture_golden.py
"""

import json
import os
import pathlib
import unittest

from ffopt import backtest, client, config, season

GOLDEN = pathlib.Path(__file__).resolve().parent / "golden" / "draft_behaviour.json"


def replay(year: int, seat: int, seed: int) -> list[str]:
    cfg = config.load()
    projections = client.projections(year)
    backtest.STRATEGIES["_golden"] = (
        lambda r, al, c, **k: backtest.pick_optimizer(r, al, c, horizon=8, **k)
    )
    ctx, _ = backtest.build_context(cfg, projections, seed=1000 * seed + seat, lam=0.7)
    ctx.waivers = season.objective_waivers([])
    roster = backtest.run_draft(ctx, {seat: "_golden"})[seat]
    return [i.player_id for i in roster]


class TestDraftBehaviourUnchanged(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN) as f:
            cls.expected = json.load(f)

    def test_golden_file_is_populated(self):
        self.assertGreaterEqual(len(self.expected), 12)
        for picks in self.expected.values():
            self.assertEqual(len(picks), config.load().rounds)

    @unittest.skipUnless(
        os.environ.get("FFOPT_GOLDEN") == "1",
        "slow (~70s); run with FFOPT_GOLDEN=1 before committing engine changes",
    )
    def test_behaviour_matches_snapshot(self):
        """Any diff means the validated backtest result must be re-established."""
        mismatches = []
        for key, expected in sorted(self.expected.items()):
            year, seat, seed = (int(x) for x in key.split("|"))
            actual = replay(year, seat, seed)
            if actual != expected:
                mismatches.append(f"{key}: {sum(a != b for a, b in zip(actual, expected))} picks differ")
        self.assertEqual(
            mismatches, [],
            "draft behaviour changed; re-run the 5-season backtest before "
            "claiming the documented result:\n  " + "\n  ".join(mismatches),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
