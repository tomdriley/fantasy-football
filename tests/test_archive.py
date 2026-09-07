"""As-of capture/replay invariants; all transport is synthetic."""

import copy
import contextlib
import dataclasses
import gzip
import io
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from ffopt import archive, client, collector, config, inseason, policies

EPOCH = 1_800_000_000_000


class Transport:
    def __init__(self, cfg):
        self.clock = EPOCH
        self.calls = []
        self.fail_path = None
        self.http_error_path = None
        self.nfl_calls = 0
        self.rollover = False
        positions = cfg.starting_positions
        # Sleeper reserves "0" for an empty slot.
        mine = [f"p{i}" for i in range(len(positions))]
        self.players = {}
        self.projections = []
        for i, (pid, slot) in enumerate(zip(mine, positions)):
            pos = "WR" if slot == "FLEX" else slot
            self.players[pid] = {
                "position": pos, "fantasy_positions": [pos], "team": "DET",
                "full_name": f"Player {i}", "injury_status": None,
            }
            self.projections.append({"player_id": pid, "stats": {"rec": 10}})
        self.players["free-def"] = {
            "position": "DEF", "fantasy_positions": ["DEF"], "team": "GB",
            "full_name": "Free defense", "injury_status": None,
        }
        self.projections.append({"player_id": "free-def", "stats": {"rec": 11}})
        rosters = [
            {"roster_id": 2, "owner_id": cfg.my_user_id, "players": mine, "reserve": []},
            {"roster_id": 1, "owner_id": "other", "players": [], "reserve": []},
        ]
        league = {
            "season": cfg.season, "status": "in_season",
            "scoring_settings": cfg.scoring_weights,
            "roster_positions": cfg.raw["roster_constraints"]["roster_positions_ordered"],
            "settings": copy.deepcopy(cfg.in_season["raw_settings"]),
        }
        base = f"/v1/league/{cfg.league_id}"
        self.payloads = {
            "/v1/state/nfl": {"season": cfg.season, "week": 1},
            base: league,
            base + "/rosters": rosters,
            base + "/matchups/1": [{"roster_id": 2, "starters": list(mine), "matchup_id": 1}],
            base + "/transactions/1": [],
            "/v1/players/nfl": self.players,
            f"/projections/nfl/{cfg.season}/1": self.projections,
            f"/scores/nfl/regular/{cfg.season}/1": [{
                "game_id": "g", "start_time": EPOCH + 86_400_000,
                "metadata": {"home_team": "DET", "away_team": "GB"},
            }],
        }

    def __call__(self, url, *, timeout=30):
        path = urlsplit(url).path
        self.calls.append(path)
        if path == self.fail_path:
            raise client.ApiError("simulated offline source")
        if path == self.http_error_path:
            return client.HttpDocument(url, self.clock, self.clock, 503, {}, b"unavailable")
        payload = self.payloads[path]
        if path == "/v1/state/nfl":
            self.nfl_calls += 1
            if self.rollover and self.nfl_calls == 2:
                payload = {**payload, "week": 2}
        return client.HttpDocument(
            url, self.clock, self.clock, 200,
            {"date": "provider timestamp", "age": "10", "etag": "same-body"},
            json.dumps(payload).encode(),
        )


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = pathlib.Path(temp.name)
        self.store = archive.Archive(self.folder / "evidence.sqlite3")
        raw = copy.deepcopy(config.load().raw)
        raw["league"]["total_agents"] = 2
        self.cfg = config.LeagueConfig(raw)
        self.transport = Transport(self.cfg)
        clock = patch.object(archive, "now_ms", side_effect=lambda: self.transport.clock)
        clock.start()
        self.addCleanup(clock.stop)

    def capture(self, **kwargs):
        return collector.collect(self.store, self.cfg, fetch=self.transport, **kwargs)


class TestCapture(ArchiveCase):
    def test_complete_inputs_and_raw_bytes_are_preserved(self):
        sid = self.capture()
        manifest = self.store.manifest(sid)
        self.assertEqual(manifest["status"], "complete")
        observations = {o["role"]: o for o in manifest["observations"]}
        self.assertEqual(set(observations), set(collector.required_roles(1)))
        players = observations["players"]
        self.assertEqual(self.store.body(players["body_hash"]), json.dumps(self.transport.players).encode())
        self.assertEqual(players["headers"]["age"], "10")
        self.assertEqual(players["origin"], "network")
        self.assertEqual(players["received_at_ms"], EPOCH)
        self.assertEqual(json.loads(self.store.body(manifest["config_hash"])), self.cfg.raw)

    def test_cached_reuse_retains_original_receipt_time(self):
        first = self.capture()
        old = {o["role"]: o for o in self.store.manifest(first)["observations"]}
        self.transport.clock += 10_000
        self.transport.calls.clear()
        second = self.capture()
        recent = {o["role"]: o for o in self.store.manifest(second)["observations"]}
        self.assertEqual(recent["players"]["origin"], "archive_reuse")
        self.assertEqual(recent["players"]["received_at_ms"], EPOCH)
        self.assertEqual(recent["players"]["recorded_at_ms"], EPOCH + 10_000)
        self.assertEqual(recent["players"]["body_hash"], old["players"]["body_hash"])
        self.assertNotIn("/v1/players/nfl", self.transport.calls)
        self.assertEqual(self.transport.calls, ["/v1/state/nfl"])  # Forced closing bookend.

    def test_expired_sources_refetched_but_daily_reference_reused(self):
        self.capture()
        self.transport.clock += 600_000
        self.transport.calls.clear()
        sid = self.capture()
        self.assertIn(f"/v1/league/{self.cfg.league_id}/rosters", self.transport.calls)
        self.assertNotIn("/v1/players/nfl", self.transport.calls)
        self.assertEqual(self.store.manifest(sid)["status"], "complete")

    def test_force_refresh_does_not_fall_back_on_failure(self):
        self.capture()
        self.transport.clock += 10_000
        self.transport.fail_path = "/v1/players/nfl"
        with self.assertRaises(collector.CaptureError) as caught:
            self.capture(refresh=True)
        failed = self.store.manifest(caught.exception.snapshot_id)
        self.assertEqual(failed["status"], "incomplete")
        player = next(o for o in failed["observations"] if o["role"] == "players")
        self.assertIsNone(player["body_hash"])
        self.assertIn("offline", player["error"])
        with self.assertRaises(collector.CaptureError):
            collector.replay(self.store, failed["id"])

    def test_http_error_body_is_retained_and_not_replayed(self):
        self.transport.http_error_path = "/v1/players/nfl"
        with self.assertRaises(collector.CaptureError) as caught:
            self.capture()
        manifest = self.store.manifest(caught.exception.snapshot_id)
        obs = next(o for o in manifest["observations"] if o["role"] == "players")
        self.assertEqual(obs["status_code"], 503)
        self.assertEqual(self.store.body(obs["body_hash"]), b"unavailable")

    def test_schema_invalid_success_response_is_not_usable(self):
        self.transport.payloads["/v1/players/nfl"] = []
        with self.assertRaises(collector.CaptureError) as caught:
            self.capture()
        self.assertEqual(self.store.manifest(caught.exception.snapshot_id)["status"], "invalid")

    def test_bad_player_payload_does_not_poison_scheduled_recovery(self):
        self.transport.payloads["/v1/players/nfl"] = []
        with self.assertRaises(collector.CaptureError):
            self.capture()
        self.transport.payloads["/v1/players/nfl"] = self.transport.players
        self.transport.clock += 600_000
        self.transport.calls.clear()
        sid = self.capture()
        self.assertIn("/v1/players/nfl", self.transport.calls)
        self.assertEqual(self.store.manifest(sid)["status"], "complete")

    def test_slow_daily_reference_does_not_expire_fast_sources_or_trap_retries(self):
        player_downloads = []

        def slow_fetch(url, *, timeout=30):
            started = self.transport.clock
            if urlsplit(url).path == "/v1/players/nfl":
                player_downloads.append(started)
                self.transport.clock += 31_000
            response = self.transport(url, timeout=timeout)
            return dataclasses.replace(response, requested_at_ms=started)

        for _ in range(3):
            sid = collector.collect(self.store, self.cfg, fetch=slow_fetch)
            self.assertEqual(self.store.manifest(sid)["status"], "complete")
            self.transport.clock += 1000
        self.assertEqual(len(player_downloads), 1)

    def test_invalid_cached_context_triggers_a_recovery_refresh(self):
        self.capture()
        self.transport.clock += 60_000
        self.transport.payloads[f"/v1/league/{self.cfg.league_id}/rosters"][0]["players"].append("new")
        self.transport.players["new"] = {
            "position": "WR", "fantasy_positions": ["WR"], "team": "DET", "full_name": "New",
        }
        with self.assertRaises(collector.CaptureError):
            self.capture()  # Old daily player map lacks the new rostered player.
        self.transport.clock += 1000
        self.transport.calls.clear()
        sid = self.capture()
        self.assertIn("/v1/players/nfl", self.transport.calls)
        self.assertEqual(self.store.manifest(sid)["status"], "complete")

    def test_free_agent_metadata_and_score_errors_prevent_complete_status(self):
        self.transport.players["free-def"] = None
        with self.assertRaises(collector.CaptureError) as caught:
            self.capture()
        self.assertEqual(self.store.manifest(caught.exception.snapshot_id)["status"], "invalid")
        self.transport.players["free-def"] = {
            "position": "DEF", "fantasy_positions": ["DEF"], "team": "GB",
        }
        self.transport.projections[-1]["stats"] = {"rec": 1e308, "rec_td": 1e308}
        self.transport.clock += 1000
        with self.assertRaises(collector.CaptureError) as caught:
            self.capture()
        self.assertEqual(self.store.manifest(caught.exception.snapshot_id)["status"], "invalid")
        self.assertEqual(self.store.evaluations(caught.exception.snapshot_id), [])

    def test_rollover_between_bookends_is_not_usable(self):
        self.transport.rollover = True
        with self.assertRaises(collector.CaptureError) as caught:
            self.capture()
        self.assertIn("changed", str(caught.exception))
        self.assertEqual(self.store.manifest(caught.exception.snapshot_id)["status"], "incomplete")

    def test_requested_old_week_preserves_a_failed_capture_not_fake_current_data(self):
        with self.assertRaises(collector.CaptureError) as caught:
            self.capture(week=2)
        self.assertEqual(self.store.manifest(caught.exception.snapshot_id)["status"], "incomplete")


class TestReplay(ArchiveCase):
    def test_replay_uses_no_network_current_configuration_or_current_clock(self):
        sid = self.capture()
        cfg, first = collector.replay(self.store, sid)
        expected = policies.compare(cfg, first, [policies.PickupFloor(2)])
        self.transport.clock += 10 * 86_400_000
        with patch.object(client, "fetch_document", side_effect=AssertionError("network")), \
             patch.object(client, "get", side_effect=AssertionError("cache/network")), \
             patch.object(config, "load", side_effect=AssertionError("current config")), \
             patch.object(inseason, "load", side_effect=AssertionError("live path")):
            archived_cfg, replay = collector.replay(self.store, sid)
            actual = policies.compare(archived_cfg, replay, [policies.PickupFloor(2)])
        self.assertEqual(replay.fetched_at_ms, EPOCH)
        self.assertEqual(replay.snapshot_id, sid)
        self.assertEqual(archive.encode(expected), archive.encode(actual))
        self.assertTrue(actual["disagreements"][0]["pickups_changed"])
        self.assertFalse(actual["disagreements"][0]["lineup_changed"])

    def test_a_later_capture_cannot_change_old_evidence(self):
        old = self.capture()
        cfg, old_state = collector.replay(self.store, old)
        result = policies.compare(cfg, old_state)
        self.transport.clock += 300_000
        self.transport.projections[0]["stats"]["rec"] = 99
        new = self.capture(refresh=True)
        self.assertNotEqual(old, new)
        cfg, again = collector.replay(self.store, old)
        self.assertEqual(policies.compare(cfg, again), result)

    def test_future_and_expired_source_times_are_rejected(self):
        sid = self.capture()
        manifest = self.store.manifest(sid)
        obs = {o["role"]: o for o in manifest["observations"]}
        obs["players"]["received_at_ms"] = EPOCH + 1
        with self.assertRaisesRegex(archive.ArchiveError, "timestamps"):
            collector.validate_observations(obs, EPOCH)
        obs["players"]["received_at_ms"] = EPOCH
        with self.assertRaisesRegex(archive.ArchiveError, "expired"):
            collector.validate_observations(obs, EPOCH + 601_000)

    def test_unfinished_capture_cannot_be_replayed(self):
        sid = self.store.begin(self.cfg, 1, "test")
        self.assertEqual(self.store.manifest(sid)["status"], "unfinished")
        with self.assertRaises(collector.CaptureError):
            collector.replay(self.store, sid)


class TestPersistence(ArchiveCase):
    def test_repeated_payloads_share_blobs(self):
        self.capture()
        before = self.store.stats()
        self.transport.clock += 5_000
        self.capture()
        self.assertEqual(self.store.stats(), before)

    def test_evidence_is_append_only_and_capture_is_sealed(self):
        sid = self.capture()
        with contextlib.closing(sqlite3.connect(self.store.path)) as db, db:
            for sql in (
                "UPDATE captures SET started_at_ms=0",
                "DELETE FROM observations",
                "UPDATE blobs SET raw_bytes=0",
            ):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    db.execute(sql)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "sealed"):
            self.store.failure(sid, "late", "https://test", requested_at_ms=EPOCH,
                               error="too late", max_age_seconds=30)

    def test_hash_verification_detects_corruption(self):
        sid = self.capture()
        key = self.store.manifest(sid)["config_hash"]
        # Deliberately bypass the update guard to simulate corrupted external storage.
        with contextlib.closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute("DROP TRIGGER immutable_blobs_update")
            db.execute("UPDATE blobs SET gzip_body=? WHERE hash=?", (gzip.compress(b"tampered"), key))
        with self.assertRaisesRegex(archive.ArchiveError, "hash mismatch"):
            self.store.body(key)

    def test_backup_is_independent_and_cannot_overwrite_an_existing_file(self):
        sid = self.capture()
        destination = self.folder / "backup.sqlite3"
        self.store.backup(destination)
        recovered = archive.Archive(destination, create=False)
        self.assertEqual(recovered.manifest(sid), self.store.manifest(sid))
        with self.assertRaises(FileExistsError):
            self.store.backup(destination)

    def test_evaluations_append_and_cannot_be_saved_for_failed_inputs(self):
        sid = self.capture()
        cfg, st = collector.replay(self.store, sid)
        report = policies.compare(cfg, st)
        a = self.store.evaluate(sid, report, "first")
        b = self.store.evaluate(sid, report, "second")
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.store.evaluations(sid)), 2)
        unfinished = self.store.begin(self.cfg, 1, "test")
        with self.assertRaises(archive.ArchiveError):
            self.store.evaluate(unfinished, report, "test")

    def test_read_existing_missing_archive_does_not_create_one(self):
        path = self.folder / "not-present"
        with self.assertRaises(FileNotFoundError):
            archive.Archive(path, create=False)
        self.assertFalse(path.exists())


class TestTransportProvenance(unittest.TestCase):
    def test_response_body_preserved_and_sensitive_headers_not_collected(self):
        class Response(io.BytesIO):
            status = 200
            headers = {
                "Content-Type": "application/json", "Date": "timestamp",
                "Age": "123", "Set-Cookie": "private", "Authorization": "private",
            }
        raw = b'{ "exact spacing": 1 }'
        with patch("urllib.request.urlopen", return_value=Response(raw)):
            response = client.fetch_document("https://example.test")
        self.assertEqual(response.body, raw)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["age"], "123")
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("authorization", response.headers)

    def test_nonfinite_json_is_not_accepted_as_evidence(self):
        with self.assertRaises(ValueError):
            collector.decode(b'{"forecast":NaN}', {})
