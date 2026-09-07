import copy
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import yaml

from ffopt import config
from scripts import refresh_rules


class TestRefreshRepairsWeeklyDrift(unittest.TestCase):
    def test_failed_replace_preserves_rules_for_live_service_readers(self):
        with tempfile.TemporaryDirectory() as folder:
            before = copy.deepcopy(config.load().raw)
            after = copy.deepcopy(before)
            after["league"]["status"] = "complete"
            path = pathlib.Path(folder) / "rules.yaml"
            path.write_text(yaml.safe_dump(before))
            with patch.object(config, "RULES_PATH", path), \
                 patch.object(refresh_rules, "build", return_value=after), \
                 patch.object(refresh_rules.os, "replace", side_effect=OSError("disk failure")), \
                 patch("sys.argv", ["refresh_rules.py"]), patch("sys.stdout"):
                with self.assertRaises(OSError):
                    refresh_rules.main()
            self.assertEqual(yaml.safe_load(path.read_text()), before)
            self.assertEqual(list(pathlib.Path(folder).glob("*.tmp")), [])

    def test_order_and_bench_capacity_changes_are_written(self):
        for change in ("bench", "order"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                cfg = config.load()
                before = copy.deepcopy(cfg.raw)
                after = copy.deepcopy(before)
                rc = after["roster_constraints"]
                if change == "bench":
                    rc["roster_positions_ordered"].append("BN")
                    rc["total_roster_size"] += 1
                    rc["bench_slots"] += 1
                else:
                    positions = rc["roster_positions_ordered"]
                    positions[0], positions[1] = positions[1], positions[0]
                path = pathlib.Path(folder) / "rules.yaml"
                path.write_text(yaml.safe_dump(before))
                with patch.object(config, "RULES_PATH", path), \
                     patch.object(refresh_rules, "build", return_value=after), \
                     patch("sys.argv", ["refresh_rules.py"]), patch("sys.stdout"):
                    self.assertEqual(refresh_rules.main(), 0)
                self.assertEqual(yaml.safe_load(path.read_text()), after)
