import unittest

from ffopt import guidance


class TestManagerDecisions(unittest.TestCase):
    def decision(self, **changes):
        arguments = {
            "usable": True, "historical": False, "changes": False,
            "missing": [], "data_warnings": [], "flagged": False, "checks_due": False,
        }
        return guidance.decisions(**(arguments | changes))

    def test_complete_lineup_has_explicit_hold_for_lineup_and_roster(self):
        result = self.decision()
        self.assertEqual(result["lineup"]["action"], "hold")
        self.assertEqual(result["roster"]["action"], "hold")
        self.assertEqual(result["roster"]["basis"], "conservative_default")
        self.assertIn("Do not add or drop", result["roster"]["instruction"])
        self.assertNotIn("optimal", result["roster"]["reason"])

    def test_lineup_changes_are_not_hidden_by_roster_hold(self):
        result = self.decision(changes=True)
        self.assertEqual(result["lineup"]["action"], "change")
        self.assertEqual(result["roster"]["action"], "hold")

    def test_questionable_means_keep_with_a_check_not_a_probability_discount(self):
        result = self.decision(flagged=True)
        self.assertEqual(result["lineup"]["action"], "hold")
        self.assertIn("Return", result["lineup"]["instruction"])
        self.assertIn("Questionable alone", result["lineup"]["reason"])
        result = self.decision(flagged=True, checks_due=True)
        self.assertEqual(result["lineup"]["action"], "check")
        self.assertIn("Keep these starters", result["lineup"]["instruction"])
        self.assertIn("replace anyone ruled out", result["lineup"]["instruction"])

    def test_gaps_cannot_be_presented_as_safe_roster_hold(self):
        result = self.decision(missing=["WR", "FLEX"])
        self.assertEqual(result["roster"]["action"], "repair")
        self.assertIn("WR, FLEX", result["roster"]["reason"])

    def test_data_warnings_prevent_all_clear_decisions(self):
        result = self.decision(data_warnings=["confirm IR eligibility"])
        self.assertEqual(result["lineup"]["action"], "repair")
        self.assertEqual(result["roster"]["action"], "repair")

    def test_unusable_and_historical_reports_never_issue_hold_or_change(self):
        for historical in (False, True):
            with self.subTest(historical=historical):
                result = self.decision(usable=False, historical=historical, changes=True, flagged=True)
                self.assertTrue(all(r["action"] == "update" and r["blocked"] for r in result.values()))

    def test_real_vacancy_has_a_named_staged_move_not_optional_hold(self):
        result = self.decision(missing=["QB"], repair_plan={
            "status": "proposed", "add": {"name": "Available QB"}, "drop": {"name": "Bench player"},
        })
        self.assertEqual(result["lineup"]["action"], "repair")
        self.assertEqual(result["roster"]["action"], "change")
        self.assertEqual(result["roster"]["title"], "Add Available QB")
        self.assertIn("drop Bench player together", result["roster"]["instruction"])
        self.assertIn("Do not drop first", result["roster"]["instruction"])

    def test_blocked_drop_has_specific_do_not_act_reason(self):
        result = self.decision(missing=["QB"], repair_plan={
            "status": "blocked", "reason": "Only injured bench assets remain.",
            "instructions": ["Do not drop an injured player based on this report."],
        })
        self.assertEqual(result["roster"]["basis"], "data_guard")
        self.assertEqual(result["roster"]["reason"], "Only injured bench assets remain.")
        self.assertIn("Do not drop", result["roster"]["instruction"])

    def test_irreparable_locked_slot_is_hold_not_an_impossible_repair(self):
        result = self.decision(locked_unavailable=["Locked player"])
        self.assertEqual(result["lineup"]["action"], "hold")
        self.assertEqual(result["roster"]["action"], "hold")
        self.assertIn("leave those slots unchanged", result["lineup"]["reason"])
        self.assertIn("cannot repair", result["roster"]["reason"])
