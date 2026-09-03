"""Behavioural acceptance tests: what a correct DRAFT looks like.

These exist because the component test suite passed 16/16 while the engine was
drafting 5.9 quarterbacks per 15-man roster. A test named
`test_quarterback_scores_most_but_is_not_most_valuable` passed the entire time --
it verified that the *valuation* understood the trap, not that the *system*
avoided it.

Every test here runs a draft and inspects the resulting roster. They encode what
a sane outcome looks like, independent of how the objective is implemented, so
that any future change to the objective is checked against behaviour rather than
against its own internal logic.
"""

from __future__ import annotations

import collections
import random
import unittest

from ffopt import availability, client, config, optimizer, pool, season, valuation


def draft_one(seat: int, *, seed: int = 0, reach: float = 1.5, bots: int = 4):
    """Run a full draft from `seat` and return our roster."""
    cfg = config.load()
    items = pool.build(client.projections(cfg.season), cfg.scoring_weights)
    baselines = valuation.compute_baselines(items, cfg)
    vor = {
        (i.player_id or i.name): valuation.value_over_replacement(i, baselines)
        for i in items
    }
    waivers = season.waiver_baselines(items, cfg)
    board = availability.consensus_order([i for i in items if i.adp is not None])[:190]

    rng = random.Random(seed)
    model = availability.OpponentModel(reach=reach, rng=rng)
    alive = list(range(len(board)))
    my_picks = set(cfg.pick_numbers(seat))
    roster: list[pool.Item] = []

    for pick_no in range(1, cfg.rounds * cfg.num_agents + 1):
        if not alive:
            break
        if pick_no in my_picks:
            sub = [board[i] for i in alive]
            recs = optimizer.recommend(
                roster, sub, cfg, seat=seat, current_pick=pick_no,
                vor=vor, waivers=waivers, num_candidates=4, trials=4,
                reach=reach, bot_seats=bots, rng=rng,
            )
            chosen = recs[0].item
            idx = alive[[board[i] for i in alive].index(chosen)]
            roster.append(chosen)
        else:
            idx = model.claim(alive, deterministic=(rng.random() < bots / 9.0))
        alive.remove(idx)
    return cfg, roster


class TestDraftBehaviour(unittest.TestCase):
    """The tests that would have caught the hoarding bugs."""

    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.roster = draft_one(seat=5, seed=11)
        cls.counts = collections.Counter(i.pos for i in cls.roster)

    def test_roster_is_full_and_legal(self):
        self.assertEqual(len(self.roster), self.cfg.rounds)
        self.assertEqual(len({id(i) for i in self.roster}), len(self.roster))

    def test_every_starting_slot_can_be_filled(self):
        for position, needed in self.cfg.dedicated_slots.items():
            self.assertGreaterEqual(
                self.counts[position], needed,
                f"cannot field {needed} {position}: roster has {self.counts[position]}",
            )

    def test_no_quarterback_hoarding(self):
        """Caught the first pathology: 5.9 QBs per roster."""
        self.assertLessEqual(self.counts["QB"], 2, f"roster: {dict(self.counts)}")

    def test_no_defense_hoarding(self):
        """Caught the second pathology: 5.5 DEFs per roster."""
        self.assertLessEqual(self.counts["DEF"], 1, f"roster: {dict(self.counts)}")

    def test_no_kicker_hoarding(self):
        self.assertLessEqual(self.counts["K"], 1, f"roster: {dict(self.counts)}")

    def test_majority_of_roster_is_flex_eligible(self):
        """RB/WR/TE fill 7 of 10 starting slots, so they should dominate."""
        flex = sum(self.counts[p] for p in self.cfg.flex_types)
        self.assertGreaterEqual(flex, 10, f"roster: {dict(self.counts)}")

    def test_scarce_types_are_claimed_late(self):
        """K and DEF carry negligible marginal value; they must not be claimed early."""
        for pick_index, item in enumerate(self.roster):
            if item.pos in ("K", "DEF"):
                self.assertGreaterEqual(
                    pick_index + 1, 9,
                    f"{item.pos} claimed at round {pick_index + 1}",
                )

    def test_first_pick_is_a_high_value_flex_type(self):
        """The Josh Allen trap: never spend pick 1 on the highest raw scorer."""
        self.assertIn(self.roster[0].pos, self.cfg.flex_types)


class TestSeasonValue(unittest.TestCase):
    """The objective itself, independent of the draft."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load()
        items = pool.build(client.projections(cls.cfg.season), cls.cfg.scoring_weights)
        cls.items = items
        cls.groups = pool.by_position(items)
        cls.waivers = season.waiver_baselines(items, cls.cfg)

    def test_bench_depth_at_flex_types_has_value(self):
        """Regression: the FLEX split bug zeroed out depth beyond int(slots)."""
        roster = self.groups["RB"][:4] + self.groups["WR"][:4]
        for position in ("RB", "WR"):
            gain = season.marginal_season_value(
                roster, self.groups[position][4], self.cfg, self.waivers
            )
            self.assertGreater(
                gain, 0.0, f"5th {position} priced at zero -- FLEX pooling is broken"
            )

    def test_backup_defense_is_worthless(self):
        roster = [self.groups["DEF"][0]]
        gain = season.marginal_season_value(
            roster, self.groups["DEF"][1], self.cfg, self.waivers
        )
        self.assertLess(gain, 5.0, "a second defense should be near-worthless")

    def test_backup_kicker_is_worthless(self):
        roster = [self.groups["K"][0]]
        gain = season.marginal_season_value(
            roster, self.groups["K"][1], self.cfg, self.waivers
        )
        self.assertLess(gain, 5.0, "a second kicker should be near-worthless")

    def test_spare_running_back_beats_backup_defense(self):
        """The comparison that drives correct late-round behaviour."""
        roster = self.groups["RB"][:3] + self.groups["WR"][:3] + [self.groups["DEF"][0]]
        rb = season.marginal_season_value(
            roster, self.groups["RB"][3], self.cfg, self.waivers
        )
        df = season.marginal_season_value(
            roster, self.groups["DEF"][1], self.cfg, self.waivers
        )
        self.assertGreater(rb, df)

    def test_starter_is_worth_more_than_bench(self):
        empty: list = []
        starter = season.marginal_season_value(
            empty, self.groups["RB"][0], self.cfg, self.waivers
        )
        full = self.groups["RB"][:8]
        bench = season.marginal_season_value(
            full, self.groups["RB"][8], self.cfg, self.waivers
        )
        self.assertGreater(starter, bench)


if __name__ == "__main__":
    unittest.main(verbosity=2)
