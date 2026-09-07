"""The observed lineup and the final lineup must not be conflated."""

import dataclasses
import json
import pathlib
import tempfile
import unittest

from ffopt import decisionlog, lineup
from tests.test_inseason import stream_state, small_cfg
from ffopt import inseason


class TestJournal(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = pathlib.Path(folder.name) / "decisions.jsonl"
        self.state = stream_state()
        self.state.league_id = "test-league"
        self.best = lineup.optimise_for(inseason.my_candidates(self.state), small_cfg())

    def write(self):
        return decisionlog.record(self.state, self.best, path=self.path)

    def test_append_does_not_overwrite_original(self):
        first = self.write()
        self.state.my_starters = ["0", "myk"]
        self.write()
        entries = list(decisionlog.entries(self.path))
        self.assertEqual(entries[0], first)
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(entries[0]["decision_id"], entries[1]["decision_id"])

    def test_preserves_ordered_slots_source_times_and_missing_values(self):
        self.state.sources = {"projections": 123}
        entry = self.write()
        self.assertEqual(entry["sources"], {"projections": 123})
        self.assertEqual([p["slot_index"] for p in entry["recommended"]], [0, 1])
        self.assertEqual(entry["observed_starters"], self.state.my_starters)
        self.assertEqual(len(entry["roster_snapshot"]), 2)

    def test_outcome_does_not_replace_recommendation(self):
        entry = self.write()
        original = self.path.read_text()
        outcome = decisionlog.score_week(
            entry, {"mydef": 1, "myk": 3}, final_starters=["mydef", "0"],
        )
        decisionlog.record_outcome(entry, outcome, recorded_at_ms=123, path=self.path)
        self.assertTrue(self.path.read_text().startswith(original))
        self.assertEqual(decisionlog.latest("2026", 1, self.path), entry)
        self.assertEqual(outcome.observed_points, 4)
        self.assertEqual(outcome.final_points, 1)

    def test_negative_advice_delta_retained(self):
        entry = self.write()
        entry["observed_starters"] = ["winner"]
        outcome = decisionlog.score_week(entry, {"mydef": 0, "myk": 0, "winner": 50})
        self.assertEqual(outcome.delta, -50)

    def test_missing_outcome_not_silently_zero(self):
        with self.assertRaisesRegex(ValueError, "missing realized"):
            decisionlog.score_week(self.write(), {})

    def test_nonexistent_log_is_empty_but_corrupt_log_errors(self):
        self.assertEqual(list(decisionlog.entries(self.path)), [])
        self.path.write_text("not json")
        with self.assertRaises(json.JSONDecodeError):
            list(decisionlog.entries(self.path))

    def test_latest_filters_league_and_roster(self):
        self.write()
        self.assertIsNone(decisionlog.latest("2026", 1, self.path, league_id="different"))
        self.assertIsNone(decisionlog.latest("2026", 1, self.path, roster_id=8))

    def test_failed_write_is_not_silent_success(self):
        with self.assertRaises(OSError):
            decisionlog.record(self.state, self.best, path=self.path.parent)
