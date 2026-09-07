"""Tests for weekly lineup selection.

The optimality claim is *verified against brute force* rather than asserted:
`lineup.optimise` uses a greedy dedicated-then-flex assignment, which is only
correct because each type's candidates are sorted descending. Rather than trust
that argument, these tests enumerate every legal lineup on randomised rosters
and check the greedy total matches the true maximum.

The remaining tests cover the failure that actually costs points in a live week:
starting somebody who cannot play.
"""

import itertools
import random
import unittest

from ffopt import config, lineup

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]
FLEX_TYPES = ["RB", "WR", "TE"]


def make(pid, pos, pts, **kw):
    return lineup.Candidate(
        player_id=str(pid), name=f"{pos}{pid}", pos=pos, points=pts, **kw
    )


def brute_force_best(candidates, slot_names=SLOTS, flex_types=FLEX_TYPES):
    """Maximum total over every legal assignment. Exponential; test-only."""
    dedicated = [s for s in slot_names if s != "FLEX"]
    n_flex = sum(1 for s in slot_names if s == "FLEX")
    by_pos = {}
    for c in candidates:
        by_pos.setdefault(c.pos, []).append(c)

    need = {}
    for s in dedicated:
        need[s] = need.get(s, 0) + 1

    pos_choices = []
    for pos, n in need.items():
        pool = by_pos.get(pos, [])
        pos_choices.append(list(itertools.combinations(pool, min(n, len(pool)))))
    best = 0.0
    for combo in itertools.product(*pos_choices):
        chosen = [c for grp in combo for c in grp]
        if len(chosen) != len(dedicated):
            continue
        used = {c.player_id for c in chosen}
        rest = [
            c for c in candidates
            if c.player_id not in used and c.pos in flex_types
        ]
        for flex in itertools.combinations(rest, min(n_flex, len(rest))):
            best = max(best, sum(c.points for c in chosen + list(flex)))
    return best


class TestOptimality(unittest.TestCase):
    def test_matches_brute_force_on_random_rosters(self):
        rng = random.Random(20260905)
        for trial in range(40):
            cands, pid = [], 0
            for pos, n in (("QB", 2), ("RB", 5), ("WR", 5), ("TE", 2), ("K", 1), ("DEF", 1)):
                for _ in range(n):
                    pid += 1
                    cands.append(make(pid, pos, round(rng.uniform(0, 30), 2)))
            got = lineup.optimise(
                cands, slot_names=SLOTS, flex_types=FLEX_TYPES
            ).total
            want = brute_force_best(cands)
            self.assertAlmostEqual(got, want, places=6, msg=f"trial {trial}")

    def test_type_minimums_bind_even_when_other_types_score_more(self):
        """2 RB must start even if every WR outscores every RB.

        This is draft bug #3 from algorithm.md section 8, in weekly form:
        pooling flex-eligible types drops binding minimums and yields a lineup
        that cannot legally be fielded.
        """
        cands = (
            [make(i, "WR", 30.0) for i in range(1, 8)]
            + [make(10 + i, "RB", 1.0) for i in range(4)]
            + [make(20, "QB", 20.0), make(21, "TE", 5.0),
               make(22, "K", 8.0), make(23, "DEF", 9.0)]
        )
        result = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        started = result.starters()
        self.assertEqual(sum(1 for c in started if c.pos == "RB"), 2)
        self.assertEqual(sum(1 for c in started if c.pos == "WR"), 4)  # 2 WR + 2 FLEX
        self.assertEqual(result.unfilled, [])

    def test_flex_prefers_the_best_remaining_eligible_type(self):
        """With a spare TE and a spare RB available, flex takes the two best."""
        cands = [
            make(1, "QB", 20), make(2, "K", 5), make(3, "DEF", 6),
            make(4, "RB", 18), make(5, "RB", 17), make(6, "RB", 16),
            make(7, "WR", 15), make(8, "WR", 14), make(9, "WR", 2),
            make(10, "TE", 13), make(11, "TE", 12),
        ]
        result = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        flex = [s.player for s in result.slots if s.name == "FLEX"]
        # TE 10 fills the dedicated TE slot; flex takes RB 6 (16) and TE 11 (12),
        # not WR 9 (2).
        self.assertEqual({p.player_id for p in flex}, {"6", "11"})


class TestNeverStartsAZero(unittest.TestCase):
    """The single most expensive avoidable error in a week."""

    def _roster(self, overrides=None):
        cands = [
            make(1, "QB", 20), make(2, "RB", 18), make(3, "RB", 17),
            make(4, "WR", 16), make(5, "WR", 15), make(6, "TE", 12),
            make(7, "RB", 11), make(8, "WR", 10), make(9, "K", 8),
            make(10, "DEF", 7),
        ]
        for pid, kw in (overrides or {}).items():
            for c in cands:
                if c.player_id == str(pid):
                    for k, v in kw.items():
                        setattr(c, k, v)
        return cands

    def test_bye_week_player_is_never_started(self):
        cands = self._roster({2: {"on_bye": True}})
        result = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        self.assertNotIn("2", [c.player_id for c in result.starters()])
        self.assertIn("bye week", dict(
            (c.player_id, r) for c, r in result.excluded
        )["2"])

    def test_hard_out_statuses_are_never_started(self):
        for status in ("IR", "Out", "PUP", "Sus", "NA", "DNR"):
            with self.subTest(status=status):
                cands = self._roster({2: {"status": status}})
                result = lineup.optimise(
                    cands, slot_names=SLOTS, flex_types=FLEX_TYPES
                )
                self.assertNotIn("2", [c.player_id for c in result.starters()])

    def test_doubtful_excluded_by_default_but_overridable(self):
        cands = self._roster({2: {"status": "Doubtful"}})
        strict = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        self.assertNotIn("2", [c.player_id for c in strict.starters()])
        loose = lineup.optimise(
            self._roster({2: {"status": "Doubtful"}}),
            slot_names=SLOTS, flex_types=FLEX_TYPES, allow_doubtful=True,
        )
        self.assertIn("2", [c.player_id for c in loose.starters()])

    def test_questionable_is_started_but_flagged(self):
        """Projections already discount these; excluding would double-count."""
        cands = self._roster({2: {"status": "Questionable"}})
        result = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        self.assertIn("2", [c.player_id for c in result.starters()])
        self.assertIn("2", [c.player_id for c in result.flagged])

    def test_unfillable_slot_is_reported_not_hidden(self):
        """A missing kicker must surface loudly: it is a guaranteed zero."""
        cands = [c for c in self._roster() if c.pos != "K"]
        result = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        self.assertIn("K", result.unfilled)

    def test_every_excluded_player_carries_a_reason(self):
        cands = self._roster({2: {"on_bye": True}, 3: {"status": "IR"}})
        result = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        self.assertEqual(len(result.excluded), 2)
        for _, reason in result.excluded:
            self.assertTrue(reason)


class TestPerPlayerLock(unittest.TestCase):
    """Slots freeze at each player's own kickoff, not at a weekly deadline."""

    def test_locked_starter_is_pinned_even_if_outscored(self):
        cands = [
            make(1, "QB", 20), make(2, "RB", 2, locked=True, started=True, current_slot=1),
            make(3, "RB", 17), make(4, "WR", 16), make(5, "WR", 15),
            make(6, "TE", 12), make(7, "RB", 30), make(8, "WR", 10),
            make(9, "K", 8), make(10, "DEF", 7),
        ]
        result = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        started = {c.player_id for c in result.starters()}
        self.assertIn("2", started, "a locked starter cannot be benched")
        pinned = [s for s in result.slots if s.pinned]
        self.assertEqual([s.player.player_id for s in pinned], ["2"])

    def test_locked_bench_player_cannot_be_promoted(self):
        cands = [
            make(1, "QB", 20), make(2, "RB", 18), make(3, "RB", 17),
            make(4, "WR", 16), make(5, "WR", 15), make(6, "TE", 12),
            make(7, "RB", 99, locked=True, started=False),
            make(8, "WR", 10), make(9, "K", 8), make(10, "DEF", 7),
        ]
        result = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        self.assertNotIn("7", [c.player_id for c in result.starters()])
        self.assertIn("locked on bench", dict(
            (c.player_id, r) for c, r in result.excluded
        )["7"])

    def test_unlocked_players_still_optimised_around_a_pin(self):
        cands = [
            make(1, "QB", 20), make(2, "RB", 5, locked=True, started=True, current_slot=1),
            make(3, "RB", 17), make(4, "WR", 16), make(5, "WR", 15),
            make(6, "TE", 12), make(7, "RB", 14), make(8, "WR", 13),
            make(9, "K", 8), make(10, "DEF", 7),
        ]
        result = lineup.optimise(cands, slot_names=SLOTS, flex_types=FLEX_TYPES)
        self.assertEqual(result.unfilled, [])
        self.assertIn("3", [c.player_id for c in result.starters()])


class TestLeagueIntegration(unittest.TestCase):
    def test_uses_the_leagues_own_slot_structure(self):
        cfg = config.load()
        cands = [
            make(1, "QB", 20), make(2, "RB", 18), make(3, "RB", 17),
            make(4, "WR", 16), make(5, "WR", 15), make(6, "TE", 12),
            make(7, "RB", 11), make(8, "WR", 10), make(9, "K", 8),
            make(10, "DEF", 7),
        ]
        result = lineup.optimise_for(cands, cfg)
        self.assertEqual(len(result.slots), cfg.raw["roster_constraints"]["starting_lineup_size"])
        self.assertEqual(result.unfilled, [])
        self.assertNotIn("BN", [s.name for s in result.slots])


class TestAssignmentSafety(unittest.TestCase):
    def test_locked_flex_is_not_moved_to_dedicated_position(self):
        pinned = make("locked", "WR", 1, locked=True, started=True, current_slot=2)
        result = lineup.optimise(
            [pinned, make("a", "WR", 20), make("b", "RB", 18), make("c", "WR", 15)],
            slot_names=["WR", "RB", "FLEX"], flex_types=FLEX_TYPES,
        )
        self.assertEqual(result.slots[2].player.player_id, "locked")
        self.assertEqual(result.slots[0].player.player_id, "a")

    def test_locked_out_player_remains_in_exact_original_slot(self):
        p = make("x", "RB", 0, locked=True, started=True, current_slot=1, status="Out")
        result = lineup.optimise(
            [p, make("y", "RB", 20)], slot_names=["RB", "FLEX"], flex_types=FLEX_TYPES,
        )
        self.assertTrue(result.slots[1].pinned)
        self.assertEqual(result.slots[1].player.player_id, "x")

    def test_locked_starter_without_slot_is_error_not_guess(self):
        with self.assertRaisesRegex(ValueError, "original slot"):
            lineup.optimise(
                [make("a", "QB", 20, locked=True, started=True)],
                slot_names=["QB"], flex_types=FLEX_TYPES,
            )

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            lineup.optimise([make(1, "QB", 3)] * 2, slot_names=["QB"], flex_types=FLEX_TYPES)

    def test_missing_forecast_not_treated_as_zero_forecast(self):
        p = make(1, "QB", None)
        result = lineup.optimise([p], slot_names=["QB"], flex_types=FLEX_TYPES)
        self.assertEqual(result.unfilled, ["QB"])
        self.assertEqual(result.excluded[0][1], "missing projection")

    def test_later_player_gets_flex_without_changing_selected_set(self):
        early = make(1, "WR", 10, kickoff_ms=100)
        late = make(2, "WR", 20, kickoff_ms=200)
        result = lineup.optimise(
            [early, late], slot_names=["WR", "FLEX"], flex_types=FLEX_TYPES,
        )
        self.assertEqual(result.total, 30)
        self.assertEqual(result.slots[1].player.player_id, "2")

    def test_multiple_eligible_positions(self):
        dual = make(1, "WR", 20, positions=("WR", "RB"))
        result = lineup.optimise(
            [dual, make(2, "WR", 18)], slot_names=["RB", "WR"], flex_types=FLEX_TYPES,
        )
        self.assertEqual(result.total, 38)

    def test_injury_practice_dnp_is_not_game_out(self):
        result = lineup.optimise(
            [make(1, "WR", 20, status="DNP")], slot_names=["WR"], flex_types=FLEX_TYPES,
        )
        self.assertEqual(len(result.starters()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
