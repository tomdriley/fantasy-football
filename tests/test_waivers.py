import copy
import dataclasses
import functools
import json
import math
import random
import unittest

from ffopt.waivers import Opportunity, WaiverModel, evaluate_waiver, solve_waivers


def model_dict(**overrides):
    return {
        "horizon": 2,
        "opportunities": [{"probability": 1.0, "reward": 4.0}],
        "success_probabilities": [1.0, 0.5],
        "wait_transitions": [[1.0, 0.0], [0.0, 1.0]],
        "spend_transitions": [[0.0, 1.0], [0.0, 1.0]],
        "label": "Explicit synthetic example, not calibrated",
        "assumptions": ["Independent stationary opportunities", "Reward is lineup utility"],
        "uncertainty": "Opportunity rewards and probabilities are hypothetical.",
        **overrides,
    }


class TestWaiverDP(unittest.TestCase):
    def test_rank_dependent_thresholds_and_current_action_values(self):
        model = WaiverModel.from_dict(model_dict())
        solution = solve_waivers(model)
        self.assertEqual(solution.values, ((0.0, 0.0), (4.0, 2.0), (6.0, 4.0)))
        high = solution.decision(rank=1, reward=1.0)
        low = solution.decision(rank=2, reward=1.0)
        self.assertEqual((high.wait_value, high.spend_value, high.threshold), (4.0, 3.0, 2.0))
        self.assertEqual((low.wait_value, low.spend_value, low.threshold), (2.0, 2.5, 0.0))
        self.assertEqual((high.action, low.action), ("wait", "spend"))
        self.assertEqual(solution.decision(rank=1, reward=2).action, "wait")
        self.assertEqual(solution.decision(rank=1, reward=2.1).action, "spend")

    def test_last_period_does_not_save_priority_for_unmodeled_weeks(self):
        solution = solve_waivers(WaiverModel.from_dict(model_dict()))
        decision = solution.decision(rank=1, reward=0.1, remaining=1)
        self.assertEqual(decision.threshold, 0.0)
        self.assertEqual(decision.action, "spend")
        self.assertEqual(decision.wait_value, 0.0)
        self.assertEqual(decision.spend_value, 0.1)

    def test_zero_horizon_has_no_spend_action_or_salvage(self):
        model = WaiverModel.from_dict(model_dict(horizon=0))
        solution = solve_waivers(model)
        decision = solution.decision(rank=1, reward=100)
        self.assertEqual(solution.values, ((0.0, 0.0),))
        self.assertEqual(decision.wait_value, 0.0)
        self.assertIsNone(decision.spend_value)
        self.assertIsNone(decision.threshold)
        self.assertEqual(decision.action, "wait")
        self.assertIn("exhausted", decision.reason)

    def test_zero_success_has_no_finite_threshold(self):
        model = WaiverModel.from_dict(model_dict(success_probabilities=[1, 0]))
        decision = evaluate_waiver(model, rank=2, reward=100)
        self.assertIsNone(decision.threshold)
        self.assertEqual(decision.spend_value, decision.wait_value)
        self.assertEqual(decision.action, "wait")
        self.assertIn("Zero success", decision.reason)

    def test_no_opportunity_event_forces_wait(self):
        model = WaiverModel.from_dict(model_dict(
            opportunities=[
                {"probability": 0.5, "reward": 0, "available": False},
                {"probability": 0.5, "reward": 10},
            ],
            success_probabilities=[0, 1],
        ))
        self.assertEqual(solve_waivers(model).values, ((0, 0), (0, 5), (2.5, 10)))

    def test_negative_rewards_and_zero_probabilities_are_valid(self):
        model = WaiverModel.from_dict(model_dict(opportunities=[
            {"probability": 0.0, "reward": 100},
            {"probability": 1.0, "reward": -10},
        ]))
        self.assertEqual(solve_waivers(model).values, ((0, 0), (0, 0), (0, 0)))
        self.assertEqual(evaluate_waiver(model, rank=1, reward=-1).action, "wait")

    def test_stochastic_transitions_match_recursive_enumeration(self):
        rng = random.Random(52)
        for trial in range(12):
            wait, spend = [], []
            for _ in range(2):
                probability = rng.random()
                wait.append([probability, 1 - probability])
                probability = rng.random()
                spend.append([probability, 1 - probability])
            model = WaiverModel.from_dict(model_dict(
                horizon=4, wait_transitions=wait, spend_transitions=spend,
                success_probabilities=[rng.random(), rng.random()],
                opportunities=[
                    {"probability": 0.2, "reward": 0, "available": False},
                    {"probability": 0.3, "reward": 2},
                    {"probability": 0.5, "reward": 7},
                ],
            ))

            @functools.lru_cache(None)
            def brute(remaining, rank):
                if remaining == 0:
                    return 0.0
                wait_value = sum(
                    probability * brute(remaining - 1, next_rank)
                    for next_rank, probability in enumerate(model.wait_transitions[rank])
                )
                spent_value = sum(
                    probability * brute(remaining - 1, next_rank)
                    for next_rank, probability in enumerate(model.spend_transitions[rank])
                )
                return sum(
                    opportunity.probability * (
                        max(wait_value, opportunity.reward * model.success_probabilities[rank] + spent_value)
                        if opportunity.available else wait_value
                    )
                    for opportunity in model.opportunities
                )

            solution = solve_waivers(model)
            for remaining, values in enumerate(solution.values):
                for rank, value in enumerate(values):
                    self.assertAlmostEqual(value, brute(remaining, rank), msg=f"trial {trial}")

    def test_uncertainty_and_assumptions_survive_json_and_output(self):
        raw = model_dict()
        original = copy.deepcopy(raw)
        model = WaiverModel.from_dict(json.loads(json.dumps(raw)))
        decision = evaluate_waiver(model, rank=1, reward=3)
        self.assertEqual(raw, original)
        self.assertEqual(decision.assumptions, tuple(raw["assumptions"]))
        self.assertEqual(decision.uncertainty, raw["uncertainty"])
        self.assertEqual(decision.model_label, raw["label"])
        self.assertIn("Exact only", decision.limitation)
        self.assertIn("not a calibrated football model", decision.limitation)
        json.dumps(dataclasses.asdict(decision), allow_nan=False)


class TestWaiverValidation(unittest.TestCase):
    def test_no_automatic_probability_model(self):
        with self.assertRaisesRegex(ValueError, "missing fields"):
            WaiverModel.from_dict({})
        for field in (
            "horizon", "opportunities", "success_probabilities",
            "wait_transitions", "spend_transitions",
        ):
            raw = model_dict()
            del raw[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "missing fields"):
                WaiverModel.from_dict(raw)
        with self.assertRaisesRegex(ValueError, "explicitly supplied"):
            solve_waivers(None)

    def test_horizon_rank_and_remaining_are_bounded_integers(self):
        for bad in (-1, 1.5, True, "2", math.inf):
            with self.subTest(horizon=bad), self.assertRaises(ValueError):
                WaiverModel.from_dict(model_dict(horizon=bad))
        solution = solve_waivers(WaiverModel.from_dict(model_dict()))
        for bad in (0, 3, -1, True, "1", 1.5):
            with self.subTest(rank=bad), self.assertRaises(ValueError):
                solution.decision(rank=bad, reward=1)
        for bad in (-1, 3, True, "1", 1.5):
            with self.subTest(remaining=bad), self.assertRaises(ValueError):
                solution.decision(rank=1, reward=1, remaining=bad)

    def test_nonnegative_finite_normalized_probabilities(self):
        for bad in (-0.1, 1.1, math.nan, math.inf, -math.inf, True, "0.5"):
            for field in ("success", "opportunity", "wait", "spend"):
                raw = model_dict()
                if field == "success":
                    raw["success_probabilities"][0] = bad
                elif field == "opportunity":
                    raw["opportunities"][0]["probability"] = bad
                else:
                    raw[f"{field}_transitions"][0][0] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    WaiverModel.from_dict(raw)
        for overrides in (
            {"opportunities": []},
            {"opportunities": [{"probability": 0.9, "reward": 1}]},
            {"wait_transitions": [[0.1, 0.1], [0, 1]]},
            {"spend_transitions": [[0, 1], [0, 0]]},
        ):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, "sum to 1"):
                WaiverModel.from_dict(model_dict(**overrides))

    def test_matrix_dimensions_and_structure(self):
        for overrides in (
            {"success_probabilities": []},
            {"success_probabilities": "1, .5"},
            {"wait_transitions": [[1, 0]]},
            {"spend_transitions": [[1], [1]]},
            {"wait_transitions": [[1, 0], None]},
            {"opportunities": [1]},
            {"opportunities": [{"probability": 1}]},
            {"opportunities": "none"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                WaiverModel.from_dict(model_dict(**overrides))
        with self.assertRaises(ValueError):
            WaiverModel.from_dict([])

    def test_rewards_must_be_finite_even_for_zero_probability_events(self):
        for bad in (math.nan, math.inf, -math.inf, None, "2", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Opportunity(probability=0, reward=bad)
            with self.subTest(current=bad), self.assertRaises(ValueError):
                evaluate_waiver(WaiverModel.from_dict(model_dict()), rank=1, reward=bad)

    def test_no_opportunity_and_metadata_validation(self):
        for overrides in (
            {"label": ""},
            {"uncertainty": None},
            {"assumptions": "not an array"},
            {"assumptions": [""]},
            {"opportunities": [{"probability": 1, "reward": 2, "available": False}]},
            {"opportunities": [{"probability": 1, "reward": 0, "available": 1}]},
            {"opportunities": [{"probability": 1, "reward": 0, "typo": True}]},
            {"typo": 1},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                WaiverModel.from_dict(model_dict(**overrides))

    def test_direct_construction_is_also_validated_and_immutable(self):
        with self.assertRaises(ValueError):
            WaiverModel(-1, (Opportunity(1, 2),), (1,), ((1,),), ((1,),))
        model = WaiverModel(1, [Opportunity(1, 2)], [1], [[1]], [[1]])
        self.assertEqual(model.wait_transitions, ((1.0,),))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            model.horizon = 2

    def test_finite_inputs_cannot_silently_overflow_outputs(self):
        model = WaiverModel(
            2, (Opportunity(1, 1e308),), (1,), ((1,),), ((1,),),
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            solve_waivers(model)


if __name__ == "__main__":
    unittest.main()
