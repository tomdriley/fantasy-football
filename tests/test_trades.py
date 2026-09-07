import copy
import dataclasses
import itertools
import math
import random
import unittest
from unittest import mock

from ffopt import config, lineup, trades


def league(slots=("RB", "WR"), capacity=3, flex=("RB", "WR", "TE"), deadline=11):
    return config.LeagueConfig({
        "roster_constraints": {
            "roster_positions_ordered": [*slots, *(["BN"] * (capacity - len(slots)))],
            "total_roster_size": capacity,
            "flex_accepts_types": list(flex),
        },
        "scoring_weights": {"rec": 1.0},
        "in_season": {"trades": {"enabled": True, "deadline_week": deadline}},
    })


def player(pid, pos, points, **kwargs):
    return lineup.Candidate(pid, pid, pos, points, **kwargs)


def rosters():
    return {
        1: [player("a1", "RB", 20), player("a2", "RB", 15), player("a3", "WR", 3)],
        2: [player("b1", "RB", 2), player("b2", "WR", 18), player("b3", "WR", 14)],
    }


def search(teams=None, cfg=None, **kwargs):
    options = {"week": 5, "horizon_label": "Explicit projected week 6 (full PPR)", **kwargs}
    return trades.find_trades(
        rosters() if teams is None else teams, 1, league() if cfg is None else cfg,
        **options,
    )


def signature(trade):
    return trade.opponent_roster_id, trade.send_ids, trade.receive_ids


def brute_trades(teams, cfg):
    """Independent small exhaustive reference with explicit drop enumeration."""
    lookup = {p.player_id: p for roster in teams.values() for p in roster}
    original = {rid: {p.player_id for p in roster} for rid, roster in teams.items()}
    before = {rid: lineup.optimise_for(roster, cfg).total for rid, roster in teams.items()}

    def after(rid, sent, received):
        retained = original[rid] - set(sent)
        ids = retained | set(received)
        excess = max(0, len(ids) - cfg.roster_size)
        best = None
        for dropped in itertools.combinations(sorted(retained), excess):
            result = lineup.optimise_for([lookup[p] for p in sorted(ids - set(dropped))], cfg)
            if not result.unfilled and (best is None or result.total > best):
                best = result.total
        return best

    results = []
    for opponent in sorted(set(teams) - {1}):
        for n_send, n_receive in ((1, 1), (2, 1), (1, 2)):
            for sent in itertools.combinations(sorted(original[1]), n_send):
                for received in itertools.combinations(sorted(original[opponent]), n_receive):
                    our_after = after(1, sent, received)
                    their_after = after(opponent, received, sent)
                    if (
                        our_after is not None and their_after is not None
                        and our_after > before[1] and their_after > before[opponent]
                    ):
                        results.append((
                            our_after - before[1], their_after - before[opponent],
                            opponent, sent, received,
                        ))
    results.sort(key=lambda result: (-result[0], -result[1], *result[2:]))
    return results[:5]


class TestTradeSearch(unittest.TestCase):
    def test_one_for_one_improves_both_teams_not_just_ours(self):
        result = search(candidate_limit=1)
        self.assertTrue(result.trades)
        self.assertEqual(signature(result.trades[0]), (2, ("a2",), ("b2",)))
        best = result.trades[0]
        self.assertEqual((best.ours.before, best.ours.after, best.ours.gain), (23, 38, 15))
        self.assertEqual((best.opponent.before, best.opponent.after, best.opponent.gain), (20, 29, 9))
        self.assertEqual(best.ours.drop_ids, ())
        self.assertEqual(best.opponent.drop_ids, ())
        for trade in result.trades:
            self.assertGreater(trade.ours.gain, 0)
            self.assertGreater(trade.opponent.gain, 0)

    def test_two_for_one_recipient_has_an_explicit_optimized_drop(self):
        teams = {
            1: [player("a", "RB", 20), player("b", "RB", 15), player("c", "WR", 2)],
            2: [player("d", "RB", 1), player("e", "WR", 18), player("f", "WR", 14)],
        }
        result = search(teams)
        paired = [t for t in result.trades if len(t.send_ids) != len(t.receive_ids)]
        self.assertTrue(paired)
        all_players = {p.player_id: p for roster in teams.values() for p in roster}
        for trade in paired:
            for rid, outgoing, incoming, impact in (
                (1, trade.send_ids, trade.receive_ids, trade.ours),
                (2, trade.receive_ids, trade.send_ids, trade.opponent),
            ):
                original = {p.player_id for p in teams[rid]}
                uncapped = (original - set(outgoing)) | set(incoming)
                final = uncapped - set(impact.drop_ids)
                self.assertEqual(len(impact.drop_ids), max(0, len(uncapped) - 3))
                self.assertTrue(set(impact.drop_ids) <= original - set(outgoing))
                self.assertFalse(set(incoming) & set(impact.drop_ids))
                self.assertLessEqual(len(final), 3)
                expected = lineup.optimise_for([all_players[p] for p in final], league())
                without_drop = lineup.optimise_for([all_players[p] for p in uncapped], league())
                self.assertFalse(expected.unfilled)
                self.assertEqual(impact.after, expected.total)
                self.assertEqual(impact.gain, expected.total - impact.before)
                self.assertEqual(impact.drop_cost, without_drop.total - expected.total)
                # A bench-only drop really has zero cost in this one-week objective.
                self.assertIn("longer-term drop costs are not valued", trade.limitation)

    def test_drop_that_would_destroy_position_coverage_is_not_proposed(self):
        teams = {
            1: [player("a", "RB", 100), player("b", "WR", 1), player("c", "WR", 0)],
            2: [player("d", "RB", 99), player("e", "RB", 98), player("f", "WR", 1)],
        }
        result = search(teams)
        for trade in result.trades:
            self.assertNotEqual((trade.send_ids, trade.receive_ids), (("c",), ("d", "e")))
        self.assertFalse(result.trades)
        self.assertIn("No mutually improving", result.reason)

    def test_free_capacity_means_no_unnecessary_drop(self):
        teams = rosters()
        result = search(teams, cfg=league(capacity=4))
        self.assertTrue(result.trades)
        self.assertTrue(any(len(t.send_ids) != len(t.receive_ids) for t in result.trades))
        for trade in result.trades:
            self.assertEqual(trade.ours.drop_ids, ())
            self.assertEqual(trade.opponent.drop_ids, ())

    def test_configured_flex_constraints_and_multiple_positions_are_used(self):
        cfg = league(("RB", "FLEX"), capacity=3, flex=("TE",))
        teams = {
            1: [
                player("a", "WR", 20, positions=("WR", "RB")),
                player("b", "RB", 15), player("c", "TE", 1),
            ],
            2: [player("d", "RB", 2), player("e", "TE", 18), player("f", "TE", 14)],
        }
        result = search(teams, cfg=cfg, candidate_limit=1)
        self.assertTrue(result.trades)
        self.assertEqual(result.trades[0].ours.before, 21)
        self.assertEqual(result.trades[0].opponent.before, 20)
        self.assertEqual(result.trades[0].ours.after, 38)
        self.assertEqual(result.trades[0].opponent.after, 29)

    def test_result_retains_projection_scope_and_explicit_limitations(self):
        label = "User scenario: week 8 optimistic full-PPR projections"
        result = search(horizon_label=label)
        self.assertEqual(result.horizon_label, label)
        self.assertIn("not rest-of-season impact", result.limitation)
        self.assertIn("title odds or acceptance probabilities", result.limitation)
        self.assertTrue(all(trade.horizon_label == label for trade in result.trades))
        for trade in result.trades:
            self.assertEqual(trade.limitation, result.limitation)

    def test_read_only_and_unique_offers_across_opponents(self):
        teams = rosters()
        teams[3] = [
            player("z1", "RB", 1), player("z2", "WR", 30), player("z3", "WR", 19),
        ]
        before = copy.deepcopy(teams)
        result = search(teams)
        self.assertEqual(teams, before)
        self.assertEqual(len({signature(t) for t in result.trades}), len(result.trades))
        for trade in result.trades:
            our_ids = {p.player_id for p in teams[1]}
            their_ids = {p.player_id for p in teams[trade.opponent_roster_id]}
            self.assertTrue(set(trade.send_ids) <= our_ids)
            self.assertTrue(set(trade.receive_ids) <= their_ids)
            all_involved = (
                *trade.send_ids, *trade.receive_ids, *trade.ours.drop_ids, *trade.opponent.drop_ids,
            )
            self.assertEqual(len(all_involved), len(set(all_involved)))
            self.assertIn((len(trade.send_ids), len(trade.receive_ids)), ((1, 1), (1, 2), (2, 1)))

    def test_bounded_pairs_all_singles_and_result_cap(self):
        teams = {
            1: [*rosters()[1], player("a4", "TE", 4)],
            2: [*rosters()[2], player("b4", "TE", 3)],
        }
        cfg = league(("RB", "WR", "FLEX"), capacity=4)
        result = search(teams, cfg=cfg, candidate_limit=2)
        self.assertEqual(result.swaps_considered, 4 * 4 + 1 * 4 + 4 * 1)
        self.assertTrue(result.pairs_truncated)
        self.assertTrue(any("top 2" in warning for warning in result.warnings))
        self.assertLessEqual(len(result.trades), 5)
        for trade in result.trades:
            if len(trade.send_ids) == 2:
                self.assertEqual(set(trade.send_ids), {"a1", "a2"})
            if len(trade.receive_ids) == 2:
                self.assertEqual(set(trade.receive_ids), {"b2", "b3"})
        result = search(candidate_limit=0, max_results=1)
        self.assertEqual(result.swaps_considered, 9)
        self.assertEqual(len(result.trades), 1)

    def test_cached_rosters_are_not_reoptimized(self):
        original = lineup.optimise_for
        keys = []

        def record(candidates, cfg):
            candidates = list(candidates)
            keys.append(tuple(sorted(p.player_id for p in candidates)))
            return original(candidates, cfg)

        with mock.patch("ffopt.trades.lineup.optimise_for", side_effect=record):
            result = search()
        self.assertTrue(result.trades)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertLess(len(keys), 2 * result.swaps_considered)

    def test_small_search_matches_exhaustive_both_team_drop_valuation(self):
        rng = random.Random(317)
        cfg = league()
        for trial in range(24):
            teams = {
                1: [
                    player("a", "RB", rng.randint(0, 30)),
                    player("b", "RB", rng.randint(0, 30)),
                    player("c", "WR", rng.randint(0, 30)),
                ],
                2: [
                    player("d", "RB", rng.randint(0, 30)),
                    player("e", "WR", rng.randint(0, 30)),
                    player("f", "WR", rng.randint(0, 30)),
                ],
            }
            expected = brute_trades(teams, cfg)
            result = search(teams, cfg)
            actual = [
                (trade.ours.gain, trade.opponent.gain, *signature(trade))
                for trade in result.trades
            ]
            self.assertEqual(actual, expected, f"trial {trial}")

    def test_unfilled_baseline_is_reported_and_never_created_after_a_trade(self):
        teams = rosters()
        teams[1][2] = player("a3", "RB", 3)
        result = search(teams, cfg=league(capacity=4))
        self.assertTrue(any("Roster 1" in warning and "WR" in warning for warning in result.warnings))
        for trade in result.trades:
            self.assertEqual(trade.ours.before_unfilled, ("WR",))


class TestTradeGuards(unittest.TestCase):
    def test_public_deadline_guard_and_search_fail_closed(self):
        cfg = league(deadline=11)
        self.assertIsNone(trades.trade_guard(cfg, 11))
        reason = trades.trade_guard(cfg, 12)
        self.assertIn("12 > deadline week 11", reason)
        result = search(cfg=cfg, week=12)
        self.assertEqual(result.trades, ())
        self.assertEqual(result.reason, reason)
        self.assertEqual(result.valuations, 0)

    def test_disabled_or_unknown_deadline_is_explicit(self):
        cfg = league()
        cfg.raw["in_season"]["trades"]["enabled"] = False
        self.assertIn("disabled", search(cfg=cfg).reason)
        self.assertIn("unknown", search(cfg=league(deadline=None)).reason)

    def test_rejects_locked_missing_or_nonfinite_even_on_bench(self):
        for attributes in (
            {"locked": True}, {"points": None}, {"points": math.nan},
            {"points": math.inf}, {"points": -math.inf}, {"points": True},
            {"points": 10 ** 400},
        ):
            for roster_id in (1, 2):
                teams = rosters()
                teams[roster_id][-1] = dataclasses.replace(teams[roster_id][-1], **attributes)
                with self.subTest(attributes=attributes, rid=roster_id), self.assertRaisesRegex(
                    ValueError, f"roster {roster_id}, player",
                ):
                    search(teams)

    def test_ineligible_reserve_and_bye_inputs_are_not_offered_silently(self):
        for attributes in (
            {"on_bye": True}, {"status": "IR"}, {"status": "Out"},
            {"status": "Doubtful"},
            {"unavailable_reason": "on reserve; activation required"},
            {"unavailable_reason": "on taxi"},
        ):
            teams = rosters()
            teams[2][0] = dataclasses.replace(teams[2][0], **attributes)
            with self.subTest(attributes=attributes), self.assertRaisesRegex(ValueError, "ineligible"):
                search(teams)

    def test_duplicate_players_within_or_between_any_rosters_are_rejected(self):
        for which in ("same", "ours", "opponents"):
            teams = rosters()
            if which == "same":
                teams[1][1] = teams[1][0]
            elif which == "ours":
                teams[2][1] = teams[1][0]
            else:
                teams[3] = [teams[2][0]]
            with self.subTest(which=which), self.assertRaisesRegex(ValueError, "duplicate player"):
                search(teams)

    def test_invalid_ids_positions_capacity_and_options(self):
        for attributes in (
            {"player_id": ""}, {"player_id": "0"}, {"player_id": " a1"}, {"pos": "IR"},
        ):
            teams = rosters()
            teams[1][0] = dataclasses.replace(teams[1][0], **attributes)
            with self.subTest(attributes=attributes), self.assertRaises(ValueError):
                search(teams)
        with self.assertRaisesRegex(ValueError, "exceeds active roster capacity"):
            search(cfg=league(capacity=2))
        with self.assertRaisesRegex(ValueError, "our roster ID"):
            search({2: rosters()[2]})
        with self.assertRaisesRegex(ValueError, "roster ID"):
            search({True: rosters()[1], 2: rosters()[2]})
        for options in (
            {"week": 0}, {"week": True}, {"candidate_limit": -1},
            {"candidate_limit": 1.5}, {"candidate_limit": True},
            {"max_results": 0}, {"max_results": 6}, {"max_results": True},
            {"horizon_label": ""}, {"horizon_label": None},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                search(**options)

    def test_no_opponents_or_no_shared_gains_has_explicit_reason(self):
        result = search({1: rosters()[1]})
        self.assertFalse(result.trades)
        self.assertIn("No mutually improving", result.reason)


if __name__ == "__main__":
    unittest.main()
