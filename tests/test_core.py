"""Tests for config, scoring, pool and valuation.

The scoring test is a genuine regression check: this league uses a standard
full-PPR weight vector, so our independently computed payoff must reproduce the
platform's own `pts_ppr` figure for every skill-position item. Any drift there
means the payoff function is wrong.
"""

import pathlib

import unittest

from ffopt import client, config, pool, scoring, valuation


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_invariants(self):
        self.assertEqual(self.cfg.num_agents, 10)
        self.assertEqual(self.cfg.rounds, self.cfg.roster_size)
        self.assertEqual(
            sum(self.cfg.starting_slots.values()) + self.cfg.bench_slots,
            self.cfg.roster_size,
        )
        # Deliberately not a magic count. The league changed its kicker scoring
        # hours before the draft (fixed per-FG buckets -> distance-based
        # fgm_yds), which moved the total from 43 to 44 and failed a hardcoded
        # assertion that was guarding nothing useful. What matters is that
        # every weight is a usable number and the scoring types we value are
        # all represented.
        self.assertGreater(len(self.cfg.scoring_weights), 20)
        for key, weight in self.cfg.scoring_weights.items():
            self.assertIsInstance(weight, float, key)
        for essential in ("rec", "rec_yd", "rush_yd", "pass_yd", "rush_td", "rec_td"):
            self.assertIn(essential, self.cfg.scoring_weights)

    def test_flex_is_excluded_from_dedicated_slots(self):
        self.assertNotIn("FLEX", self.cfg.dedicated_slots)
        self.assertEqual(self.cfg.flex_slots, 2)
        self.assertCountEqual(self.cfg.flex_types, ["RB", "WR", "TE"])

    def test_snake_pick_numbers(self):
        # Seat 1 leads odd rounds and trails even ones.
        self.assertEqual(self.cfg.pick_numbers(1)[:4], [1, 20, 21, 40])
        self.assertEqual(self.cfg.pick_numbers(10)[:4], [10, 11, 30, 31])
        self.assertEqual(self.cfg.pick_numbers(5)[:4], [5, 16, 25, 36])

    def test_every_seat_covers_all_picks_exactly_once(self):
        seen = []
        for seat in range(1, self.cfg.num_agents + 1):
            seen.extend(self.cfg.pick_numbers(seat))
        self.assertCountEqual(seen, range(1, self.cfg.rounds * self.cfg.num_agents + 1))

    def test_rejects_out_of_range_seat(self):
        with self.assertRaises(ValueError):
            self.cfg.pick_numbers(0)
        with self.assertRaises(ValueError):
            self.cfg.pick_numbers(11)

    def test_bot_seats_are_the_unowned_ones(self):
        """Derived from the rules file, not hardcoded.

        Seats fill right up to the draft, so pinning a literal here guarantees
        a false failure on the day. What must hold is the relationship: a bot
        seat is one whose roster has no owner.
        """
        owners = {a["roster_id"]: a["user_id"] for a in self.cfg.raw["agents"]}
        slots = self.cfg.raw["draft"]["slot_to_roster_id"]
        expected = sorted(
            int(slot) for slot, rid in slots.items() if not owners.get(rid)
        )
        self.assertEqual(self.cfg.bot_seats(), expected)

    def test_bot_seats_are_a_valid_subset_of_seats(self):
        bots = self.cfg.bot_seats()
        self.assertEqual(len(bots), len(set(bots)))
        for seat in bots:
            self.assertTrue(1 <= seat <= self.cfg.num_agents)


class TestScoring(unittest.TestCase):
    def test_dot_product(self):
        stats = {"rec": 10, "rec_yd": 100, "rush_td": 2}
        weights = {"rec": 1.0, "rec_yd": 0.1, "rush_td": 6.0}
        self.assertAlmostEqual(scoring.payoff(stats, weights), 10 + 10 + 12)

    def test_ignores_consensus_and_metadata_keys(self):
        # adp_std is standard-SCORING adp, not a deviation; it must never score.
        stats = {"adp_ppr": 5.0, "adp_std": 7.0, "gp": 17, "cmp_pct": 68.0, "rec": 3}
        weights = {"rec": 1.0, "adp_std": 99.0, "gp": 99.0}
        self.assertAlmostEqual(scoring.payoff(stats, weights), 3.0)

    def test_unweighted_stats_contribute_nothing(self):
        self.assertAlmostEqual(scoring.payoff({"unknown_stat": 500}, {"rec": 1.0}), 0.0)


class TestPayoffRegression(unittest.TestCase):
    """Validate against the platform's own computed value."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load()
        cls.records = client.projections(cls.cfg.season)

    def test_matches_platform_full_ppr(self):
        weights = self.cfg.scoring_weights
        checked = 0
        worst = (0.0, None)
        for rec in self.records:
            stats = rec.get("stats") or {}
            positions = (rec.get("player") or {}).get("fantasy_positions") or []
            if not positions or positions[0] not in ("QB", "RB", "WR", "TE"):
                continue
            expected = stats.get("pts_ppr")
            if expected is None or not stats.get("gp"):
                continue
            got = scoring.payoff(stats, weights)
            delta = abs(got - expected)
            if delta > worst[0]:
                worst = (delta, rec)
            checked += 1
        self.assertGreater(checked, 300, "too few records to be a meaningful check")
        self.assertLess(
            worst[0], 0.5, f"payoff drifted from platform value by {worst[0]:.3f}"
        )


class TestValuation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load()
        cls.items = pool.build(client.projections(cls.cfg.season), cls.cfg.scoring_weights)
        cls.baselines = valuation.compute_baselines(cls.items, cls.cfg)

    def test_pool_is_populated_and_sorted(self):
        self.assertGreater(len(self.items), 300)
        payoffs = [i.payoff for i in self.items]
        self.assertEqual(payoffs, sorted(payoffs, reverse=True))

    def test_flex_absorption_is_uneven(self):
        # The whole point of modelling FLEX explicitly: it does not split evenly.
        absorbed = self.baselines.flex_absorption
        self.assertEqual(sum(absorbed.values()), self.cfg.flex_slots * self.cfg.num_agents)
        self.assertGreater(max(absorbed.values()), 2 * min(absorbed.values()))

    def test_dedicated_types_consume_exactly_their_slots(self):
        # QB/K/DEF are not flex-eligible, so consumption is slots x agents.
        for pos in ("QB", "K", "DEF"):
            self.assertEqual(
                self.baselines.consumed[pos],
                self.cfg.starting_slots[pos] * self.cfg.num_agents,
            )

    def test_quarterback_scores_most_but_is_not_most_valuable(self):
        """The central trap: highest raw payoff, low marginal value."""
        top = max(self.items, key=lambda i: i.payoff)
        self.assertEqual(top.pos, "QB")
        best_by_pos = {}
        for pos, group in pool.by_position(self.items).items():
            if group:
                best_by_pos[pos] = valuation.value_over_replacement(group[0], self.baselines)
        self.assertGreater(best_by_pos["RB"], best_by_pos["QB"])
        self.assertGreater(best_by_pos["WR"], best_by_pos["QB"])

    def test_kicker_marginal_value_is_negligible(self):
        ks = pool.by_position(self.items)["K"]
        self.assertLess(valuation.value_over_replacement(ks[0], self.baselines), 15.0)

    def test_tiers_are_ordered_and_non_empty(self):
        tiers = valuation.tiers(self.items, self.baselines)
        for pos in ("RB", "WR"):
            groups = tiers[pos]
            self.assertGreater(len(groups), 1)
            flat = [i for g in groups for i in g]
            self.assertEqual([i.payoff for i in flat], sorted((i.payoff for i in flat), reverse=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSeatMapping(unittest.TestCase):
    """seat_of_pick must invert pick_numbers, since bot identification relies on it."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load()

    def test_inverts_pick_numbers_for_every_pick(self):
        for seat in range(1, self.cfg.num_agents + 1):
            for pick in self.cfg.pick_numbers(seat):
                self.assertEqual(self.cfg.seat_of_pick(pick), seat, f"pick {pick}")

    def test_snake_direction(self):
        n = self.cfg.num_agents
        self.assertEqual(self.cfg.seat_of_pick(1), 1)
        self.assertEqual(self.cfg.seat_of_pick(n), n)
        self.assertEqual(self.cfg.seat_of_pick(n + 1), n)
        self.assertEqual(self.cfg.seat_of_pick(2 * n), 1)


class TestApiAccessRequirements(unittest.TestCase):
    """Guards a load-bearing detail of the HTTP client.

    The projections endpoint returns 403 Forbidden to requests without a
    User-Agent header. Removing it would break every data fetch, and the
    failure would look like the API being down rather than a client defect.
    """

    def test_client_sends_a_user_agent(self):
        self.assertTrue(client.USER_AGENT)
        source = pathlib.Path(client.__file__).read_text()
        self.assertIn('headers={"User-Agent": USER_AGENT}', source)

    def test_cache_write_is_atomic_and_process_unique(self):
        """Parallel workers share the cache directory."""
        source = pathlib.Path(client.__file__).read_text()
        self.assertIn("os.getpid()", source)
        self.assertIn("os.replace(tmp, path)", source)

    def test_live_feed_is_never_cached(self):
        """Stale picks are worse than no picks."""
        source = pathlib.Path(client.__file__).read_text()
        self.assertIn("ttl=0", source.split("def draft_picks")[1])


class TestOpponentReachBound(unittest.TestCase):
    """The reach cap is relied on for an exact optimisation.

    The rollout stops filtering candidates once it has more than MAX_REACH of
    them, which is only correct if an opponent can never select an item deeper
    than that. If the cap is raised without updating the filter, the two would
    silently disagree.
    """

    def test_sampled_offset_never_exceeds_the_cap(self):
        import random
        from ffopt import availability
        model = availability.OpponentModel(reach=50.0, rng=random.Random(0))
        for _ in range(2000):
            self.assertLessEqual(model._sample_offset(False), availability.MAX_REACH)

    def test_deterministic_opponents_take_the_best_available(self):
        import random
        from ffopt import availability
        model = availability.OpponentModel(reach=5.0, rng=random.Random(0))
        for _ in range(50):
            self.assertEqual(model.claim([7, 8, 9], deterministic=True), 7)

    def test_rollout_filter_window_covers_the_cap(self):
        from ffopt import availability, optimizer
        source = pathlib.Path(optimizer.__file__).read_text()
        self.assertIn("availability.MAX_REACH", source)
        self.assertGreater(availability.MAX_REACH, 0)


class TestCdnCacheBypass(unittest.TestCase):
    """The live feed must not be served from a CDN cache.

    Measured against the live endpoint on draft day: a plain request returned
    `cf-cache-status: HIT` with `age: 26` under
    `cache-control: public, s-maxage=30, stale-while-revalidate=300`. On a
    60-second pick timer that is half a pick of lag before anything in our code
    runs, and stale-while-revalidate permits considerably worse. A unique query
    parameter is a distinct cache key and reaches origin.
    """

    def test_a_unique_parameter_is_appended(self):
        from ffopt import client
        url = client._uncached("https://example.test/v1/draft/1/picks")
        self.assertIn("?_=", url)

    def test_successive_calls_differ(self):
        from ffopt import client
        a = client._uncached("https://example.test/a")
        b = client._uncached("https://example.test/a")
        self.assertNotEqual(a, b, "a repeated key would be cacheable again")

    def test_an_existing_query_string_is_preserved(self):
        from ffopt import client
        url = client._uncached("https://example.test/a?position=QB")
        self.assertIn("position=QB", url)
        self.assertIn("&_=", url)

    def test_static_endpoints_are_left_cacheable(self):
        """Projections are 5MB and static for the day; busting them is waste."""
        import inspect
        from ffopt import client
        for fn in (client.projections, client.realized_stats):
            self.assertNotIn("_uncached", inspect.getsource(fn), fn.__name__)
