import dataclasses
import datetime
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from ffopt import client, reminders
from scripts import week
from tests.test_inseason import stream_state, small_cfg, SUNDAY_4PM


class TestBriefing(unittest.TestCase):
    def test_no_unverified_historical_gain_or_guarantee(self):
        cfg = small_cfg()
        cfg.raw["league"] = {"name": "Test"}
        output = "\n".join(week.render(stream_state(), cfg))
        self.assertNotIn("5.6", output)
        self.assertIn("one-week gains, not proven edges", output)
        self.assertIn("confirm Free Agent vs Waiver", output)
        self.assertIn("No transactions submitted", output)
        self.assertIn("HOLD OPTIONAL MOVES", output)
        self.assertIn("comparisons do not approve an add/drop", output)

    def test_uses_daylight_saving_zone_in_winter(self):
        january = datetime.datetime(2027, 1, 1, 18, tzinfo=datetime.timezone.utc)
        self.assertIn("13:00 EST", week.when(int(january.timestamp() * 1000)))

    def test_calendar_has_utc_events_stable_ids_and_alarms(self):
        st = stream_state()
        data = reminders.calendar(st)
        self.assertIn("\r\n", data)
        self.assertIn("TRIGGER:-PT90M", data)
        self.assertIn("TRIGGER:-PT15M", data)
        self.assertIn("DTSTART:", data)
        self.assertEqual(data.count("BEGIN:VEVENT"), 1)
        self.assertEqual(data, reminders.calendar(st))
        self.assertTrue(all(len(line.encode()) <= 75 for line in data.split("\r\n")))
        self.assertNotIn("BEGIN:VEVENT", reminders.calendar(
            dataclasses.replace(st, fetched_at_ms=SUNDAY_4PM + 1)
        ))

    def test_source_failure_returns_error_not_guessed_week(self):
        with patch("sys.argv", ["week.py"]), patch.object(
            client, "nfl_state", side_effect=client.ApiError("offline")
        ), patch("sys.stderr"):
            self.assertEqual(week.main(), 2)

    def test_live_command_logs_and_exports_calendar(self):
        from ffopt import config, inseason
        cfg = small_cfg()
        cfg.raw["league"] = {"name": "Test"}
        with tempfile.TemporaryDirectory() as folder:
            log = pathlib.Path(folder) / "log.jsonl"
            ics = pathlib.Path(folder) / "week.ics"
            with patch("sys.argv", ["week.py", "--log-path", str(log), "--calendar", str(ics)]), \
                 patch.object(config, "load", return_value=cfg), \
                 patch.object(client, "nfl_state", return_value={"week": 1}), \
                 patch.object(inseason, "load", return_value=stream_state()), \
                 patch("sys.stdout"):
                self.assertEqual(week.main(), 0)
            self.assertIn('"kind": "decision"', log.read_text())
            self.assertIn("VALARM", ics.read_text())


class TestOutcomeCommand(unittest.TestCase):
    def test_missing_historical_rules_do_not_fall_back_to_current_weights(self):
        from ffopt import config, decisionlog
        legacy = {
            "roster_id": 2, "recommended": [{"player_id": "dropped"}],
            "observed_starters": ["dropped"],
        }
        with patch.object(decisionlog, "latest", return_value=legacy), \
             patch.object(client, "scores", return_value=[{"status": "complete"}]), \
             patch.object(client, "matchups", return_value=[{
                 "roster_id": 2, "starters": ["x"], "players_points": {"x": 1},
             }]):
            with self.assertRaisesRegex(ValueError, "captured scoring rules"):
                week.evaluate(config.load(), 1, pathlib.Path("unused"))

    def test_actual_final_lineup_and_override_are_persisted_separately(self):
        from ffopt import config
        cfg = config.load()
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "decisions.jsonl"
            entry = {
                "kind": "decision", "decision_id": "original", "schema": 2,
                "league_id": cfg.league_id, "season": cfg.season, "week": 1, "roster_id": 2,
                "recommended": [{"player_id": "a"}], "observed_starters": ["b"],
            }
            path.write_text(json.dumps(entry) + "\n")
            with patch.object(client, "scores", return_value=[{"status": "complete"}]), \
                 patch.object(client, "matchups", return_value=[{
                     "roster_id": 2, "starters": ["c"], "custom_points": 77,
                     "points": 3, "players_points": {"a": 5, "b": 4, "c": 3},
                 }]):
                result = week.evaluate(cfg, 1, path)
            self.assertEqual(result["recommended_points"], 5)
            self.assertEqual(result["observed_points"], 4)
            self.assertEqual(result["final_points"], 3)
            self.assertEqual(result["official_matchup_points"], 77)
            self.assertEqual(json.loads(path.read_text().splitlines()[-1]), result)

    def test_unfinished_week_is_not_evaluated(self):
        from ffopt import config, decisionlog
        with patch.object(decisionlog, "latest", return_value={"decision_id": "x"}), \
             patch.object(client, "scores", return_value=[{"status": "in_progress"}]):
            with self.assertRaisesRegex(ValueError, "not complete"):
                week.evaluate(config.load(), 1, pathlib.Path("unused"))
