"""Guards the in-season action rules read from the generated YAML.

These settings decide *which game is being played*. Each one, read wrongly,
silently invalidates a different policy:

  * waiver_type    -- a priority queue is not a budget. Bidding logic against a
                      rolling-priority league would be nonsense.
  * pick_trading   -- without it, draft capital cannot be offered in a trade,
                      which removes the usual rebuild currency.
  * reserve_allow_* -- if the IR slot takes only true-IR designations, stashing
                      an OUT player there is impossible and the slot is dead.

They are therefore asserted against the YAML rather than trusted.
"""

import unittest
import pathlib
import tempfile
from unittest.mock import patch

from ffopt import client, config


class TestInSeasonRules(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_league_reports_in_season(self):
        self.assertTrue(self.cfg.is_in_season)

    def test_waivers_are_rolling_priority_not_faab(self):
        """The distinction that decides the whole claim algorithm."""
        self.assertEqual(self.cfg.waiver_type, "rolling_priority")
        # The league still publishes waiver_budget: 100, which reads as a
        # spendable resource and is not one under this waiver type.
        self.assertFalse(self.cfg.waiver_budget_is_active)
        budget = (self.cfg.in_season["waivers"] or {}).get("budget")
        self.assertTrue(
            budget is None or budget > 0,
            "a budget is published; it must stay flagged inactive, not removed",
        )

    def test_dropped_players_clear_waivers_before_free_agency(self):
        self.assertEqual(self.cfg.waiver_clear_days, 2)

    def test_trades_are_player_for_player_only(self):
        self.assertTrue(self.cfg.trades_enabled)
        self.assertEqual(self.cfg.trade_deadline_week, 11)
        self.assertFalse(
            self.cfg.draft_pick_trading,
            "pick trading is disabled; trade search must not offer draft capital",
        )

    def test_trade_deadline_precedes_the_playoffs(self):
        """The structural trap: the roster is frozen before the games that decide it."""
        self.assertLess(self.cfg.trade_deadline_week, self.cfg.playoff_week_start)

    def test_reserve_extra_status_flags_are_disabled(self):
        self.assertEqual(self.cfg.reserve_slots, 1)
        self.assertFalse(self.cfg.reserve_extra_status_flags_enabled)
        allows = self.cfg.in_season["reserve"]["allows"]
        self.assertTrue(
            all(v is False for v in allows.values()),
            f"a reserve_allow_* flag is now set; IR eligibility changed: {allows}",
        )

    def test_no_in_game_substitutions(self):
        """Lock is total: once a player kicks off, the slot cannot be changed."""
        moves = self.cfg.in_season["roster_moves"]
        self.assertEqual(moves["max_in_game_subs"], 0)
        self.assertFalse(moves["bench_locked"])
        self.assertFalse(moves["adds_disabled"])

    def test_playoff_structure(self):
        self.assertEqual(self.cfg.playoff_week_start, 15)
        self.assertEqual(self.cfg.playoff_teams, 6)
        self.assertEqual(self.cfg.regular_season_weeks, 14)

    def test_playoff_week_boundary(self):
        self.assertFalse(self.cfg.is_playoff_week(14))
        self.assertTrue(self.cfg.is_playoff_week(15))
        self.assertTrue(self.cfg.is_playoff_week(17))

    def test_majority_of_the_league_reaches_the_playoffs(self):
        """6 of 10 qualify, which is why regular-season variance is cheap."""
        self.assertGreater(self.cfg.playoff_teams, self.cfg.num_agents / 2)


class TestInSeasonAccessorsDegradeGracefully(unittest.TestCase):
    """A rules file generated before the season must not crash the accessors."""

    def test_missing_in_season_block_yields_empty_defaults(self):
        cfg = config.LeagueConfig({
            "league": {"status": "pre_draft"},
            "season_structure": {},
        })
        self.assertEqual(cfg.in_season, {})
        self.assertFalse(cfg.is_in_season)
        self.assertEqual(cfg.waiver_type, "unknown")
        self.assertFalse(cfg.waiver_budget_is_active)
        self.assertIsNone(cfg.waiver_clear_days)
        self.assertFalse(cfg.trades_enabled)
        self.assertEqual(cfg.reserve_slots, 0)
        self.assertEqual(cfg.regular_season_weeks, 0)
        self.assertFalse(cfg.is_playoff_week(15))


class TestSortKeyGuard(unittest.TestCase):
    """MEASURED: an invalid `order_by` is accepted by the API and silently
    returns unprojected players first, which reads exactly like a broken feed.

    Reproduce:
      curl '.../projections/nfl/2025/5?season_type=regular&position[]=RB&order_by=ppr'
      -> HTTP 200, first entries have stats == {'adp_dd_ppr': ...} and no pts_ppr

    The guard converts that silent failure into a loud one. These are pure unit
    tests: the URL is validated before any request is made.
    """

    def test_rejects_the_key_that_actually_bit_us(self):
        with self.assertRaises(client.InvalidSortKey):
            client.weekly_projections(2025, 5, order_by="ppr")
        with self.assertRaises(client.InvalidSortKey):
            client.weekly_stats(2025, 5, order_by="ppr")

    def test_error_names_the_valid_alternatives(self):
        with self.assertRaises(client.InvalidSortKey) as ctx:
            client._ordered("http://x?a=1", "nonsense")
        self.assertIn("pts_ppr", str(ctx.exception))

    def test_accepts_documented_keys(self):
        for key in ("pts_ppr", "pts_half_ppr", "pts_std", "adp_std"):
            self.assertIn(f"order_by={key}", client._ordered("http://x?a=1", key))

    def test_none_omits_the_parameter(self):
        self.assertEqual(client._ordered("http://x?a=1", None), "http://x?a=1")

    def test_pts_ppr_is_the_default_for_weekly_calls(self):
        """Defaulting to a *valid* key is what stops the trap recurring."""
        import inspect
        for fn in (client.weekly_projections, client.weekly_stats):
            self.assertEqual(
                inspect.signature(fn).parameters["order_by"].default, "pts_ppr"
            )


class TestInSeasonCacheTTLs(unittest.TestCase):
    """A completed week is immutable; live league state is not.

    Caching a rival's roster for hours would make every recommendation stale,
    and re-fetching the 14 MB player map on a decision path would be slow.
    """

    def test_live_state_is_cached_briefly(self):
        self.assertLessEqual(client.LIVE_TTL, 300)

    def test_completed_weeks_are_cached_far_longer_than_open_ones(self):
        self.assertGreater(client.FINAL_TTL, client.WEEK_TTL * 24)

    def test_open_week_projections_expire_within_the_day(self):
        self.assertLessEqual(client.WEEK_TTL, 12 * 3600)

    def test_expired_inseason_data_does_not_hide_network_failure(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            client, "CACHE_DIR", pathlib.Path(folder)
        ), patch.object(client, "_fetch", side_effect=client.ApiError("offline")):
            client._cache_path("test").write_text('{"old": true}')
            with self.assertRaises(client.ApiError):
                client.get("https://invalid.test", "test", ttl=0, allow_stale=False)
            # Preserve the draft client's pre-existing fallback policy.
            self.assertEqual(client.get("https://invalid.test", "test", ttl=0), {"old": True})

    def test_refresh_bypasses_local_cache_and_updates_it(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            client, "CACHE_DIR", pathlib.Path(folder)
        ), patch.object(client, "_fetch", return_value={"new": True}) as fetch:
            client._cache_path("test").write_text('{"old": true}')
            value = client.get("https://invalid.test", "test", refresh=True, allow_stale=False)
            self.assertEqual(value, {"new": True})
            fetch.assert_called_once()
            self.assertEqual(client.get("https://invalid.test", "test"), {"new": True})

    def test_sort_changes_do_not_share_cache_order(self):
        with patch.object(client, "get", return_value=[] ) as fetch:
            client.weekly_projections(2025, 1, order_by="pts_std")
            key1 = fetch.call_args.args[1]
            client.weekly_projections(2025, 1, order_by="pts_ppr")
            self.assertNotEqual(key1, fetch.call_args.args[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
