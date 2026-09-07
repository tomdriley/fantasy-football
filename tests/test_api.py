import asyncio
import json
import os
import re
import threading
import time
import uuid
from unittest.mock import patch

import yaml
import httpx
from fastapi.testclient import TestClient

from ffopt import archive, client as sleeper_client
from ffopt.api import create_app
from tests.test_archive import ArchiveCase
from tests.test_jobs import wait_for


class ApiCase(ArchiveCase):
    def setUp(self):
        super().setUp()
        self.rules = self.folder / "rules.yaml"
        self.rules.write_text(yaml.safe_dump(self.cfg.raw))
        self.app = create_app(
            archive_path=self.store.path, jobs_path=self.folder / "jobs.sqlite3",
            rules_path=self.rules, api_token="test-only-token", start_worker=False,
            queue_capacity=2,
        )
        self.client = TestClient(
            self.app, headers={"Authorization": "Bearer test-only-token"},
            client=("127.0.0.1", 50000),
        )


class TestRestApi(ApiCase):
    def test_health_and_protected_status(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        status = self.client.get("/api/v1/status")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["mode"], "token-protected")
        self.assertEqual(status.json()["storage"]["unique_payloads"], 0)
        self.assertEqual(self.client.get("/readyz").status_code, 503)

    def test_token_required_for_all_data_routes(self):
        for path in ("/api/v1/status", "/api/v1/snapshots", "/api/v1/jobs", "/api/v1/openapi.json", "/api/v1/advice"):
            result = self.client.get(path, headers={"Authorization": "Bearer incorrect"})
            self.assertEqual(result.status_code, 401)
            self.assertEqual(result.json()["error"]["code"], "unauthorized")

    def test_manager_advice_routes_keep_history_separate(self):
        from ffopt.evaluation import evaluate_snapshot
        sid = self.capture()
        evaluate_snapshot(self.store, sid, 1000)
        current = self.client.get("/api/v1/advice")
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["mode"], "current")
        self.assertTrue(current.json()["freshness"]["usable"])
        self.assertTrue(current.json()["pickups"])  # Baseline, not the shadow's empty list.
        history = self.client.get(f"/api/v1/advice/{sid}")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["mode"], "historical")
        self.assertFalse(history.json()["freshness"]["usable"])

    def test_frontend_html_gets_a_fresh_matching_style_nonce(self):
        root = self.folder / "built-ui"
        root.mkdir()
        (root / "index.html").write_text(
            '<html><meta name="csp-nonce" content="__FFOPT_CSP_NONCE__"><div id="root"></div></html>'
        )
        app = create_app(
            archive_path=self.store.path, jobs_path=self.folder / "nonce-jobs.sqlite3",
            rules_path=self.rules, api_token="test-only-token", start_worker=False,
            frontend_path=root,
        )
        with TestClient(app, client=("127.0.0.1", 1)) as browser:
            a, b = browser.get("/"), browser.get("/")
        nonce = re.search(r'content="([^"]+)"', a.text).group(1)
        self.assertNotIn("__FFOPT_CSP_NONCE__", a.text)
        self.assertIn(f"'nonce-{nonce}'", a.headers["content-security-policy"])
        self.assertEqual(len(a.headers.get_list("content-security-policy")), 1)
        self.assertNotEqual(a.text, b.text)
        self.assertNotIn("'unsafe-inline'", a.headers["content-security-policy"])
        self.assertEqual(a.headers["cache-control"], "no-store")

    def test_loopback_only_when_token_not_configured(self):
        with patch.dict(os.environ):
            os.environ.pop("FFOPT_API_TOKEN", None)
            app = create_app(
                archive_path=self.store.path, jobs_path=self.folder / "local-jobs.sqlite3",
                rules_path=self.rules, start_worker=False,
            )
        local = TestClient(app, client=("127.0.0.1", 1))
        remote = TestClient(app, client=("192.0.2.10", 1))
        self.assertEqual(local.get("/api/v1/status").status_code, 200)
        self.assertEqual(remote.get("/api/v1/status").status_code, 403)

    def test_bracketed_ipv6_loopback_host_and_port_are_supported(self):
        # TestClient's transport also splits IPv6 at the first colon. Use the
        # native ASGI transport to test the actual host/origin contract.
        async def check():
            transport = httpx.ASGITransport(app=self.app, client=("::1", 50000))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://[::1]:8787",
                headers={"Authorization": "Bearer test-only-token"},
            ) as client:
                self.assertEqual((await client.get("/healthz")).status_code, 200)
                self.assertEqual((await client.get("/api/v1/status")).status_code, 200)
                response = await client.post(
                    "/api/v1/collections", json={}, headers={"Origin": "http://[::1]:8787"},
                )
                self.assertEqual(response.status_code, 202, response.text)
        asyncio.run(check())

    def test_malformed_or_disallowed_hosts_are_rejected(self):
        for host in ("evil.example", "user@localhost", "localhost/extra", "[::1]:bad", "localhost:99999"):
            with self.subTest(host=host):
                self.assertEqual(self.client.get("/healthz", headers={"Host": host}).status_code, 400)

    def test_snapshot_and_evaluation_are_served_without_network(self):
        sid = self.capture()
        from ffopt.evaluation import evaluate_snapshot
        evaluate_snapshot(self.store, sid, 2)
        with patch.object(sleeper_client, "fetch_document", side_effect=AssertionError("network")):
            result = self.client.get(f"/api/v1/snapshots/{sid}")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["snapshot"]["id"], sid)
        self.assertEqual(result.json()["latest_evaluation"]["report"]["snapshot_id"], sid)
        self.assertEqual(len(self.client.get("/api/v1/snapshots").json()["items"]), 1)
        self.assertEqual(len(self.client.get(f"/api/v1/snapshots/{sid}/evaluations").json()["items"]), 1)

    def test_missing_resources_and_invalid_requests(self):
        missing = str(uuid.uuid4())
        self.assertEqual(self.client.get(f"/api/v1/snapshots/{missing}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{missing}").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/snapshots?limit=0").status_code, 422)
        self.assertEqual(self.client.post("/api/v1/collections", json={"unknown": True}).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/collections", json={"minimum_pickup_gain": -1}).status_code, 422)

    def test_jobs_are_accepted_idempotently_and_queue_is_bounded(self):
        first = self.client.post("/api/v1/collections", json={"refresh": False},
                                 headers={"Idempotency-Key": "operation"})
        self.assertEqual(first.status_code, 202, first.text)
        body = first.json()
        self.assertEqual(body["job"]["status"], "queued")
        self.assertNotIn("payload", body["job"])
        again = self.client.post("/api/v1/collections", json={"refresh": False},
                                 headers={"Idempotency-Key": "operation"})
        self.assertEqual(again.json()["job"]["id"], body["job"]["id"])
        self.assertEqual(self.client.post("/api/v1/collections", json={"refresh": True},
                         headers={"Idempotency-Key": "operation"}).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/collections", json={}).status_code, 202)
        overflow = self.client.post("/api/v1/collections", json={})
        self.assertEqual(overflow.status_code, 429)
        self.assertEqual(overflow.headers["retry-after"], "5")
        self.assertEqual(self.client.get(body["poll_url"]).json()["job"]["id"], body["job"]["id"])

    def test_failed_capture_cannot_be_evaluated(self):
        self.transport.fail_path = "/v1/players/nfl"
        from ffopt import collector
        with self.assertRaises(collector.CaptureError) as caught:
            self.capture()
        result = self.client.post(f"/api/v1/snapshots/{caught.exception.snapshot_id}/evaluations", json={})
        self.assertEqual(result.status_code, 409)

    def test_cross_origin_and_oversized_mutations_rejected(self):
        self.assertEqual(self.client.post("/api/v1/collections", json={},
                         headers={"Origin": "https://untrusted.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/collections", content="{}",
                         headers={"Content-Type": "text/plain"}).status_code, 415)
        too_large = self.client.post("/api/v1/collections", content=b"x" * 65537,
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(too_large.status_code, 413)
        self.assertEqual(self.app.state.service.jobs.stats()["queued"], 0)

    def test_response_headers_and_machine_readable_api_schema(self):
        response = self.client.get("/api/v1/status")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        schema = self.client.get("/api/v1/openapi.json")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("/api/v1/collections", schema.json()["paths"])
        self.assertIn("Job", schema.json()["components"]["schemas"])
        response_schema = schema.json()["paths"]["/api/v1/collections"]["post"]["responses"]["422"]
        self.assertEqual(
            response_schema["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ErrorEnvelope",
        )


class TestApiWorker(ApiCase):
    def test_slow_job_does_not_block_health_or_job_reads(self):
        entered, release = threading.Event(), threading.Event()
        def handler(job):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test release timeout")
            return {"snapshot_id": "test", "evaluation_id": "test"}
        runner = self.app.state.service.runner
        runner.handler = handler
        runner.start()
        try:
            response = self.client.post("/api/v1/collections", json={})
            self.assertEqual(response.status_code, 202)
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.client.get("/healthz").status_code, 200)
            self.assertEqual(self.client.get(response.json()["poll_url"]).json()["job"]["status"], "running")
        finally:
            release.set()
            runner.stop()

    def test_collection_runs_in_background_and_produces_a_browseable_snapshot(self):
        self.app.state.service.runner.start()
        self.addCleanup(self.app.state.service.runner.stop)
        with patch.object(sleeper_client, "fetch_document", side_effect=self.transport):
            response = self.client.post("/api/v1/collections", json={"minimum_pickup_gain": 2})
            self.assertEqual(response.status_code, 202, response.text)
            poll = response.json()["poll_url"]
            wait_for(self, lambda: self.client.get(poll).json()["job"]["status"] in ("succeeded", "failed"))
        job = self.client.get(poll).json()["job"]
        self.assertEqual(job["status"], "succeeded", job)
        detail = self.client.get(f"/api/v1/snapshots/{job['result']['snapshot_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["latest_evaluation"]["report"]["policies"]), 2)

    def test_job_failure_surfaces_preserved_invalid_capture(self):
        self.transport.fail_path = "/v1/players/nfl"
        self.app.state.service.runner.start()
        self.addCleanup(self.app.state.service.runner.stop)
        with self.assertLogs("ffopt.jobs", level="ERROR"), \
             patch.object(sleeper_client, "fetch_document", side_effect=self.transport):
            response = self.client.post("/api/v1/collections", json={})
            poll = response.json()["poll_url"]
            wait_for(self, lambda: self.client.get(poll).json()["job"]["status"] == "failed")
        job = self.client.get(poll).json()["job"]
        self.assertIsNotNone(job["snapshot_id"])
        self.assertIn("not usable", job["error"])
        detail = self.client.get(f"/api/v1/snapshots/{job['snapshot_id']}").json()
        self.assertEqual(detail["snapshot"]["status"], "incomplete")
