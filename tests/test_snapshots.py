import contextlib
import copy
import io
import json
import pathlib
from unittest.mock import patch

from ffopt import archive, client, collector, config, inseason, policies
from scripts import snapshots, week
from tests.test_archive import ArchiveCase


class TestSnapshotCommands(ArchiveCase):
    def test_collect_and_replay_commands_persist_comparisons(self):
        output = io.StringIO()
        with patch.object(client, "fetch_document", side_effect=self.transport), \
             patch.object(config, "load", return_value=self.cfg), \
             contextlib.redirect_stdout(output):
            status = snapshots.main([
                "--db", str(self.store.path), "collect", "--min-pickup-gain", "2",
            ])
        self.assertEqual(status, 0)
        first = json.loads(output.getvalue())
        sid = first["snapshot_id"]
        self.assertEqual(first["report"]["snapshot_id"], sid)
        self.assertEqual(len(self.store.evaluations(sid)), 1)
        output = io.StringIO()
        with patch.object(client, "fetch_document", side_effect=AssertionError("network")), \
             patch.object(config, "load", side_effect=AssertionError("current rules")), \
             contextlib.redirect_stdout(output):
            status = snapshots.main([
                "--db", str(self.store.path), "replay", sid, "--min-pickup-gain", "2",
            ])
        self.assertEqual(status, 0)
        second = json.loads(output.getvalue())
        self.assertEqual(first["report"], second["report"])
        self.assertNotEqual(first["evaluation_id"], second["evaluation_id"])
        self.assertEqual(len(self.store.evaluations(sid)), 2)

    def test_missing_archive_errors_without_creating_file(self):
        path = self.folder / "missing"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(snapshots.main(["--db", str(path), "list"]), 2)
        self.assertFalse(path.exists())

    def test_week_offline_replay_never_logs_as_a_live_decision(self):
        sid = self.capture()
        log = self.folder / "should-not-exist.jsonl"
        output = io.StringIO()
        with patch("sys.argv", [
            "week.py", "--snapshot", sid, "--archive", str(self.store.path), "--log-path", str(log),
        ]), patch.object(client, "fetch_document", side_effect=AssertionError("network")), \
             patch.object(config, "load", side_effect=AssertionError("current config")), \
             contextlib.redirect_stdout(output):
            self.assertEqual(week.main(), 0)
        self.assertIn("OFFLINE REPLAY", output.getvalue())
        self.assertIn(sid, output.getvalue())
        self.assertFalse(log.exists())

    def test_replay_cannot_accidentally_refresh_or_export_live_alarms(self):
        for flag in ("--refresh", "--log"):
            with patch("sys.argv", ["week.py", "--snapshot", "id", flag]), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(week.main(), 2)

    def test_week_capture_links_journal_to_complete_inputs(self):
        log = self.folder / "decisions.jsonl"
        with patch("sys.argv", [
            "week.py", "--capture", "--archive", str(self.store.path),
            "--log-path", str(log), "--quiet",
        ]), patch.object(client, "fetch_document", side_effect=self.transport), \
             patch.object(config, "load", return_value=self.cfg), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(week.main(), 0)
        entry = json.loads(log.read_text())
        manifest = self.store.manifest(entry["snapshot_id"])
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(entry["scoring_weights"], self.cfg.scoring_weights)

    def test_incomplete_capture_is_visible_but_no_evaluation_is_saved(self):
        self.transport.fail_path = "/v1/players/nfl"
        with patch.object(client, "fetch_document", side_effect=self.transport), \
             patch.object(config, "load", return_value=self.cfg), \
             contextlib.redirect_stderr(io.StringIO()):
            status = snapshots.main(["--db", str(self.store.path), "collect"])
        self.assertEqual(status, 2)
        sid = self.store.list()[0]["id"]
        self.assertEqual(self.store.evaluations(sid), [])


class TestPolicyIsolation(ArchiveCase):
    def test_baseline_is_unchanged_by_shadow_threshold(self):
        sid = self.capture()
        cfg, state = collector.replay(self.store, sid)
        before = copy.deepcopy(state)
        baseline = policies.compare(cfg, state)
        compared = policies.compare(cfg, state, [policies.PickupFloor(2)])
        self.assertEqual(baseline["policies"][0], compared["policies"][0])
        self.assertEqual(state, before)
        self.assertEqual(compared["policies"][1]["role"], "shadow-only")
        self.assertEqual(compared, json.loads(archive.encode(compared)))

    def test_one_policy_cannot_mutate_inputs_for_the_next(self):
        class Mutator(policies.ExpectedPoints):
            def recommend(self, cfg, state):
                state.players["free-def"]["full_name"] = "mutated"
                cfg.raw["league"]["name"] = "mutated"
                return super().recommend(cfg, state)

        cfg, state = collector.replay(self.store, self.capture())
        original = copy.deepcopy(state)
        result = policies.compare(cfg, state, [
            Mutator(name="mutator"), policies.PickupFloor(0),
        ])
        self.assertEqual(state, original)
        self.assertEqual(
            result["policies"][2]["recommendation"]["pickups"][0]["add"]["name"], "Free defense"
        )
        self.assertNotEqual(cfg.raw["league"]["name"], "mutated")

    def test_duplicate_identity_and_invalid_threshold_rejected(self):
        cfg, state = collector.replay(self.store, self.capture())
        with self.assertRaises(ValueError):
            policies.compare(cfg, state, [policies.ExpectedPoints()])
        for threshold in (float("nan"), float("inf"), -1, True):
            with self.assertRaises(ValueError):
                policies.PickupFloor(threshold)

    def test_code_version_change_is_disclosed_not_hidden(self):
        sid = self.capture()
        with patch.object(collector, "engine_fingerprint", return_value="new-code"):
            result = snapshots.evaluation(self.store, sid)
        self.assertFalse(result["report"]["same_engine_as_capture"])
        self.assertEqual(result["report"]["engine_fingerprint"], "new-code")
