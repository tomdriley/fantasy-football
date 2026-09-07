import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from ffopt import client, config, trades
from scripts import scenarios
from tests.test_inseason import stream_state, small_cfg


class TestScenarioCommands(unittest.TestCase):
    def test_waiver_command_uses_explicit_model(self):
        model = {
            "horizon": 2,
            "opportunities": [{"probability": 1, "reward": 4}],
            "success_probabilities": [1, 0],
            "wait_transitions": [[1, 0], [0, 1]],
            "spend_transitions": [[0, 1], [0, 1]],
            "label": "Synthetic test; not a league calibration",
        }
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "model.json"
            path.write_text(json.dumps(model))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = scenarios.main(["waiver", "--model", str(path), "--rank", "1", "--reward", "8"])
            self.assertEqual(status, 0)
            self.assertIn("assum", output.getvalue().lower())

    def test_waiver_refuses_missing_probabilities(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "model.json"
            path.write_text('{"horizon": 3}')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(scenarios.main([
                    "waiver", "--model", str(path), "--rank", "1", "--reward", "8",
                ]), 2)

    def test_trade_scenario_cannot_erase_current_locks(self):
        with self.assertRaisesRegex(ValueError, "future week"):
            scenarios.trade_scenario(config.load(), stream_state(), 1)

    def test_missing_future_projections_block_instead_of_assuming_zeros(self):
        with patch.object(trades, "trade_guard", return_value=None), \
             patch.object(client, "weekly_projections", return_value=[]):
            with self.assertRaisesRegex(ValueError, "no future-week"):
                scenarios.trade_scenario(config.load(), stream_state(), 2)
