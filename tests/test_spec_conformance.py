"""Conformance tests binding docs/algorithm.md to the implementation.

An audit document that has drifted from the code is worse than no document: it
invites review effort to be spent on a system that does not exist. These tests
pin the quantitative claims in the algorithm spec so that changing the code
without updating the doc fails the suite.

Each test names the section of docs/algorithm.md it enforces.
"""

from __future__ import annotations

import pathlib
import unittest

from ffopt import client, config, optimizer, pool, season

DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "algorithm.md"


class TestSpecConstants(unittest.TestCase):
    """docs/algorithm.md sections 1, 2.2, 2.3 and 5."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load()

    def test_calendar_constants(self):
        self.assertEqual(season.WEEKS, 18)
        self.assertEqual(season.BYE_WEEKS, 1)

    def test_league_structure_matches_spec(self):
        self.assertEqual(self.cfg.flex_slots, 2)
        self.assertEqual(set(self.cfg.flex_types), {"RB", "WR", "TE"})

    def test_waiver_threshold_equals_total_picks(self):
        """Spec section 5: threshold = rounds x agents, exactly."""
        self.assertEqual(
            season.WAIVER_THRESHOLD, self.cfg.rounds * self.cfg.num_agents
        )

    def test_accessible_slots_formula(self):
        """Spec section 2.3: a(tau) = d(tau) + F if flex-eligible."""
        for position, expected in (
            ("RB", 4), ("WR", 4), ("TE", 3), ("QB", 1), ("K", 1), ("DEF", 1)
        ):
            self.assertEqual(
                season.accessible_slots(position, self.cfg), expected, position
            )

    def test_unavailability_includes_bye(self):
        """Spec section 2.2: q(tau) = (GAMES_MISSED + 1) / W."""
        self.assertAlmostEqual(season.unavailability("DEF"), 1 / 18)
        self.assertAlmostEqual(season.unavailability("QB"), (3.4 + 1) / 18)

    def test_games_missed_covers_every_type(self):
        self.assertEqual(
            set(season.GAMES_MISSED), {"QB", "RB", "WR", "TE", "K", "DEF"}
        )

    def test_documented_rates_match_code(self):
        """The rate table in the spec must equal the constants in the code."""
        text = DOC.read_text()
        self.assertIn("QB 3.4, RB 2.4, WR 3.0, TE 2.6, K 1.8, DEF 0.0", text)
        for position, value in (
            ("QB", 3.4), ("RB", 2.4), ("WR", 3.0), ("TE", 2.6), ("K", 1.8), ("DEF", 0.0)
        ):
            self.assertAlmostEqual(season.GAMES_MISSED[position], value, msg=position)


class TestSpecBehaviour(unittest.TestCase):
    """docs/algorithm.md sections 2.4, 3.2 and 7."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load()
        items = pool.build(client.projections(cls.cfg.season), cls.cfg.scoring_weights)
        cls.groups = pool.by_position(items)
        cls.waivers = season.waiver_baselines(items, cls.cfg)

    def test_bench_value_capped_at_surplus_over_free_agent(self):
        """Spec 2.4 term (c): a backup at a 1-slot, deep type is worth ~nothing."""
        gain = season.marginal_season_value(
            [self.groups["DEF"][0]], self.groups["DEF"][1], self.cfg, self.waivers
        )
        self.assertLess(gain, 5.0)

    def test_depth_at_multi_slot_type_beats_depth_at_single_slot_type(self):
        """Spec 2.4: the product of both factors is what drives behaviour."""
        roster = self.groups["RB"][:3] + self.groups["WR"][:3]
        spare_rb = season.marginal_season_value(
            roster, self.groups["RB"][3], self.cfg, self.waivers
        )
        backup_def = season.marginal_season_value(
            roster + [self.groups["DEF"][0]],
            self.groups["DEF"][1], self.cfg, self.waivers,
        )
        self.assertGreater(spare_rb, backup_def)

    def test_feasibility_constraint_is_load_bearing(self):
        """Spec 3.2: fires only when picks remaining <= mandatory slots left."""
        roster = self.groups["WR"][:14]
        required = optimizer.mandatory_filter(roster, self.cfg, 1)
        self.assertIsNotNone(required)
        self.assertIn("K", required)
        self.assertIsNone(optimizer.mandatory_filter(roster, self.cfg, 10))

    def test_feasibility_constraint_silent_on_empty_roster_early(self):
        self.assertIsNone(optimizer.mandatory_filter([], self.cfg, 15))

    def test_unfilled_slot_count_is_correct(self):
        roster = self.groups["WR"][:3]
        missing = optimizer.unfilled_slots(roster, self.cfg)
        self.assertNotIn("WR", missing)
        self.assertEqual(missing.get("K"), 1)
        self.assertEqual(missing.get("RB"), 2)


class TestSpecHonesty(unittest.TestCase):
    """The spec must keep disclosing its own weak points."""

    def setUp(self):
        raw = DOC.read_text()
        self.text = raw
        # Prose wraps across lines and inside blockquotes, so strip leading
        # quote markers before normalising whitespace.
        lines = [ln.lstrip().removeprefix("> ").removeprefix(">") for ln in raw.splitlines()]
        self.flat = " ".join(" ".join(lines).split())

    def test_declares_validation_status_prominently(self):
        """The doc must state its validation status up front, whatever it is.

        Whatever the status is, it belongs above the fold. It has read
        UNVALIDATED, then GATE FAILED, and now records a conditional result.
        """
        banner = self.flat[: self.flat.index("## 1. Notation")]
        self.assertTrue(
            any(k in banner for k in (
                "UNVALIDATED", "GATE FAILED", "CONDITIONALLY VALIDATED", "VALIDATED")),
            "the validation status banner has been removed or softened",
        )

    def test_does_not_overclaim_against_the_baseline(self):
        """A positive result is the easiest thing to overstate.

        The doc must keep recording that the edge is unstable, that it depends
        on forecast quality that is unknown before the draft, and that the
        baseline is strong rather than trivial.
        """
        self.assertIn("The edge is not stable", self.flat)
        self.assertIn("unknown ahead of the draft", self.flat)
        self.assertIn("not myopic", self.flat)

    def test_records_the_out_of_sample_design(self):
        """Which seasons were tuning and which were held out must stay explicit."""
        self.assertIn("tuned on 2025", self.flat.lower())
        self.assertIn("out-of-sample", self.flat.lower())

    def test_flags_the_open_risks(self):
        """Remaining weaknesses must stay visible rather than be papered over."""
        self.assertIn("one vendor and one era", self.flat)
        self.assertIn("Design-out-of-sample is not established", self.flat)
        self.assertIn("optimizer's curse", self.flat.lower().replace("\u2019", "'"))

    def test_records_the_excluded_seasons(self):
        """Excluding data is the easiest place to hide a favourable result."""
        self.assertIn("2020 is unusable", self.flat)
        self.assertIn("78%", self.flat)

    def test_keeps_the_adversarial_checks(self):
        """Placebo and mixed-population results must stay reported."""
        self.assertIn("Placebo", self.flat)
        self.assertIn("Mixed population", self.flat)

    def test_records_the_harness_lesson(self):
        """The most transferable finding of the project."""
        self.assertIn("audit the harness before", self.flat)

    def test_reports_the_clustering_caveat(self):
        """Configurations are correlated; precision must not be overstated."""
        self.assertIn("clustered", self.flat.lower())

    def test_names_the_weakest_parameter(self):
        self.assertIn("Waiver contention rank", self.flat)

    def test_records_the_adp_std_trap(self):
        self.assertIn("not a standard deviation", self.flat)

    def test_keeps_the_trivial_heuristic_baseline(self):
        """The most likely way this project fails must stay documented."""
        self.assertIn("trivial heuristic", self.flat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
