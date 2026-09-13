import asyncio
from datetime import datetime, timezone
from email.message import Message
import io
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import psycopg
from fastapi.testclient import TestClient
from psycopg_pool import PoolClosed, PoolTimeout, TooManyRequests

from hosting.app import Settings, create_app
from hosting.auth import AUTH_PHASE, WRITE_PHASE, Identity
from hosting.database import DatabaseUnavailable, PostgresSampleReader
from hosting.write import MAX_BODY_BYTES, SCRIPT_PATH, WRITE_PAGE, WRITE_PATH
from hosting.write_database import (
    INSERT_MARKER, MARKER, POLICY_EXPRESSION, ROLE_CHECK, SELECT_MARKER,
    PostgresMarkerStore, create_writer, owner_key, public_marker, writer_settings,
)
from scripts import check_hosted_app as smoke

HOST = "stage.example.test"
ORIGIN = f"https://{HOST}"
OWNER = Identity("google", "synthetic-owner")
OTHER = Identity("google", "synthetic-other")
UNAPPROVED = Identity("google", "synthetic-unapproved")
TIMESTAMP = datetime(2026, 9, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent


def headers(identity=OWNER):
    return {
        "X-MS-CLIENT-PRINCIPAL-IDP": identity.provider, "X-MS-CLIENT-PRINCIPAL-ID": identity.subject,
        "Origin": ORIGIN, "Content-Type": "application/json", "X-FFOPT-Write": "record-v1",
        "Sec-Fetch-Site": "same-origin",
    }


def marker_row(identity=OWNER):
    return {"owner_key": owner_key(identity), "value": MARKER, "created_at": TIMESTAMP}


def settings():
    return Settings(
        environment="stage", release="a" * 40, allowed_hosts=(HOST,), phase=WRITE_PHASE,
        auth_allowlist=frozenset({OWNER, OTHER}),
    )


class TestHostingWriteHttp(unittest.TestCase):
    def setUp(self):
        self.writer = MagicMock(spec=PostgresMarkerStore)
        self.writer.status.return_value = {"recorded": False, "marker": None}
        self.writer.record.return_value = public_marker([marker_row()], owner_key(OWNER))
        self.reader = MagicMock(spec=PostgresSampleReader)
        self.app = create_app(settings(), reader=self.reader, writer=self.writer)
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(self.client.close)

    def test_authorized_status_and_post_use_only_trusted_platform_identity(self):
        for identity in (OWNER, OTHER):
            response = self.client.get(WRITE_PATH, headers=headers(identity))
            self.assertEqual(response.json(), {"recorded": False, "marker": None})
            self.writer.status.assert_called_with(identity)
            response = self.client.post(WRITE_PATH, headers=headers(identity), json={"action": "record"})
            self.assertEqual(response.status_code, 200)
            self.writer.record.assert_called_with(identity)
            for private in (OWNER.subject, OTHER.subject, owner_key(OWNER), "provider", "subject"):
                self.assertNotIn(private, response.text)
        self.assertEqual(self.client.head(WRITE_PATH, headers=headers()).content, b"")

    def test_anonymous_unapproved_and_malformed_headers_never_reach_store(self):
        for identity_headers, code in (
            ({}, 401), (headers(UNAPPROVED), 403),
            ({**headers(), "X-MS-CLIENT-PRINCIPAL-IDP": "forged"}, 401),
            ({**headers(), "X-MS-CLIENT-PRINCIPAL-ID": "person@example.test"}, 401),
        ):
            for path in (WRITE_PATH, WRITE_PAGE, SCRIPT_PATH):
                self.assertEqual(self.client.get(path, headers=identity_headers).status_code, code)
            self.assertEqual(
                self.client.post(WRITE_PATH, headers=identity_headers, json={"action": "record"}).status_code, code,
            )
        self.writer.status.assert_not_called()
        self.writer.record.assert_not_called()

    def test_browser_scripts_and_public_reads_do_not_mutate(self):
        for path in ("/healthz", "/readyz", "/fantasy-football/", "/fantasy-football/api/status",
                     WRITE_PAGE, SCRIPT_PATH, WRITE_PATH):
            response = self.client.get(path, headers=headers())
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("access-control-allow-origin", response.headers)
        self.writer.record.assert_not_called()
        self.writer.check.assert_called_once_with()
        page = self.client.get(WRITE_PAGE, headers=headers())
        self.assertIn('src="/fantasy-football/static/test-write.js"', page.text)
        self.assertNotIn("<form", page.text)
        csp = page.headers["content-security-policy"]
        for directive in ("script-src 'self'", "connect-src 'self'", "form-action 'none'"):
            self.assertIn(directive, csp)
        self.assertNotIn("unsafe-", csp)

    def test_every_unrelated_mutation_and_preflight_is_denied(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"):
            for path in (WRITE_PATH, WRITE_PATH + "/", WRITE_PAGE, SCRIPT_PATH, "/fantasy-football/api/sample",
                         "/fantasy-football/api/session", "/healthz", "/other"):
                if method == "POST" and path == WRITE_PATH:
                    continue
                response = self.client.request(method, path, headers=headers(), json={"action": "record"})
                self.assertEqual(response.status_code, 405, (method, path))
                self.assertNotIn("access-control-allow-origin", response.headers)
        self.writer.record.assert_not_called()

    def test_strict_origin_custom_header_fetch_metadata_and_media_type(self):
        cases = [
            ("Origin", None), ("Origin", ""), ("Origin", "null"), ("Origin", "https://evil.example"),
            ("Origin", ORIGIN + "/"), ("Origin", ORIGIN + ":443"), ("Origin", ORIGIN.upper()),
            ("Origin", ORIGIN + ".evil.example"), ("Origin", ORIGIN.replace("https:", "http:")),
            ("Origin", ORIGIN + " https://evil.example"), ("X-FFOPT-Write", None), ("X-FFOPT-Write", "other"),
            ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Site", "same-site"), ("Sec-Fetch-Site", "none"),
            ("Content-Type", "application/x-www-form-urlencoded"), ("Content-Type", "multipart/form-data"),
            ("Content-Type", "text/plain"), ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Encoding", "gzip"),
        ]
        for name, value in cases:
            values = headers()
            if value is None:
                values.pop(name, None)
            else:
                values[name] = value
            with self.subTest(name=name, value=value):
                self.assertEqual(self.client.post(WRITE_PATH, headers=values, content=b'{"action":"record"}').status_code, 403)
        self.writer.record.assert_not_called()
        values = headers()
        values.pop("Sec-Fetch-Site")
        response = self.client.post(WRITE_PATH, headers={
            **values, "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http",
        }, json={"action": "record"})
        self.assertEqual(response.status_code, 200)
        values["Origin"] = "https://evil.example"
        self.assertEqual(self.client.post(WRITE_PATH, headers={
            **values, "X-Forwarded-Host": "evil.example",
        }, json={"action": "record"}).status_code, 403)

    def test_duplicate_security_headers_and_malformed_lengths_are_rejected(self):
        for name in ("Origin", "Content-Type", "X-FFOPT-Write", "Sec-Fetch-Site", "Content-Length"):
            values = list(headers().items())
            if name == "Content-Length":
                values += [(name, "19"), (name, "19")]
            else:
                values.append((name.lower(), headers()[name]))
            self.assertEqual(self.client.post(WRITE_PATH, headers=values, content=b'{"action":"record"}').status_code, 403)
        for value in ("-1", "invalid", "99999999999999999", str(MAX_BODY_BYTES + 1)):
            self.assertEqual(self.client.post(WRITE_PATH, headers={
                **headers(), "Content-Length": value,
            }, content=b'{"action":"record"}').status_code, 403)
        self.writer.record.assert_not_called()

    def test_exact_json_action_rejects_identity_free_text_and_unknown_fields(self):
        cases = [
            b"", b"{}", b"null", b"[]", b"true", b"1", b'"record"', b"{",
            b'{"action":"delete"}', b'{"action":"record","subject":"synthetic-other"}',
            b'{"action":"record","value":"free text"}', b'{"action":"record","extra":null}',
            b'{"action":"other","action":"record"}', b'{"action":true}', b'{"action":NaN}',
            b'\xff', b'{"action":"record"} {}',
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(self.client.post(WRITE_PATH, headers=headers(), content=value).status_code, 400)
        self.assertEqual(self.client.post(
            WRITE_PATH + "?subject=synthetic-other", headers=headers(), json={"action": "record"},
        ).status_code, 400)
        self.writer.record.assert_not_called()

    def test_body_limit_accepts_exact_bound_and_rejects_overflow(self):
        body = b'{"action":"record"}'
        exact = body + b" " * (MAX_BODY_BYTES - len(body))
        self.assertEqual(self.client.post(WRITE_PATH, headers=headers(), content=exact).status_code, 200)
        self.writer.record.reset_mock()
        self.assertEqual(self.client.post(
            WRITE_PATH, headers=headers(), content=exact + b" ",
        ).status_code, 403)
        self.writer.record.assert_not_called()

    def test_database_errors_have_no_success_fallback_or_private_details(self):
        for method, operation in (("GET", self.writer.status), ("POST", self.writer.record)):
            operation.side_effect = DatabaseUnavailable("writer_operation_failed")
            with self.assertLogs("hosting.app", level="WARNING") as logs:
                response = self.client.request(method, WRITE_PATH, headers=headers(),
                                               content=b'{"action":"record"}' if method == "POST" else None)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(OWNER.subject, response.text + "".join(logs.output))
            self.assertNotIn('"recorded":true', response.text)

    def test_lifespan_closes_both_pools(self):
        with self.client:
            self.writer.open.assert_called_once_with()
            self.reader.open.assert_called_once_with()
        self.writer.close.assert_called_once_with()
        self.reader.close.assert_called_once_with()

    def test_hosted_smoke_requires_platform_denial_of_anonymous_and_forged_post(self):
        for strips in (True, False):
            post_count = 0
            def open_response(req, timeout):
                nonlocal post_count
                incoming = dict(req.headers)
                if strips:
                    incoming = {key: value for key, value in incoming.items() if not key.lower().startswith("x-ms-")}
                if req.get_method() == "POST":
                    post_count += 1
                response = self.client.request(req.get_method(), req.full_url, headers=incoming,
                                               content=req.data, follow_redirects=False)
                body = io.BytesIO(response.content)
                body.headers = Message()
                for key, value in response.headers.items():
                    body.headers[key] = value
                body.status = response.status_code
                if response.status_code >= 400:
                    raise HTTPError(req.full_url, response.status_code, "", body.headers, body)
                return body
            with patch.object(smoke.request.OpenerDirector, "open", side_effect=open_response):
                if strips:
                    smoke.check_release(ORIGIN, "a" * 40, "stage", expected_phase=WRITE_PHASE)
                    self.assertEqual(post_count, 2)
                else:
                    with self.assertRaises(smoke.CheckFailed):
                        smoke.check_release(ORIGIN, "a" * 40, "stage", expected_phase=WRITE_PHASE)
        self.writer.record.assert_not_called()

    def test_phase_settings_and_no_implicit_writer_activation(self):
        for phase in ("deployment", "database-readonly", AUTH_PHASE):
            values = {
                "FFOPT_HOSTING_PHASE": phase, "FFOPT_WRITE_DB_PASSWORD": "test-only",
                "FFOPT_HOSTING_ENVIRONMENT": "stage", "FFOPT_HOSTING_RELEASE": "a" * 40,
                "FFOPT_HOSTING_ALLOWED_HOSTS": HOST,
            }
            with self.assertRaises(ValueError):
                Settings.from_environment(values)
            with self.assertRaises(ValueError):
                create_app(Settings(
                    environment="stage", release="a" * 40, allowed_hosts=(HOST,), phase=phase,
                ), reader=self.reader if phase != "deployment" else None, writer=self.writer)
        with self.assertRaises(ValueError):
            Settings(phase=WRITE_PHASE)
        with self.assertRaises(ValueError):
            Settings(environment="stage", release="a" * 40, allowed_hosts=(HOST, "other.test"), phase=WRITE_PHASE)
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            create_app(settings(), reader=self.reader)


class TestHostingWriteStream(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MagicMock(spec=PostgresMarkerStore)
        self.app = create_app(settings(), reader=MagicMock(spec=PostgresSampleReader), writer=self.store)

    async def request(self, chunks, extra_headers=None, delay=0):
        messages = []
        calls = 0
        async def receive():
            nonlocal calls
            calls += 1
            if delay:
                await asyncio.sleep(delay)
            return chunks.pop(0)
        async def send(message):
            messages.append(message)
        values = {**headers(), **(extra_headers or {})}
        scope = {
            "type": "http", "http_version": "1.1", "method": "POST", "scheme": "https",
            "path": WRITE_PATH, "raw_path": WRITE_PATH.encode(), "query_string": b"",
            "root_path": "", "server": (HOST, 443), "client": ("127.0.0.1", 1234),
            "headers": [(b"host", HOST.encode()), *[
                (key.lower().encode(), value.encode()) for key, value in values.items()
            ]],
        }
        await self.app(scope, receive, send)
        return messages[0]["status"], calls

    async def test_chunked_or_unadvertised_body_overflow_stops_reading(self):
        for extra in ({}, {"Transfer-Encoding": "chunked"}, {"Content-Length": "1"}):
            chunks = [
                {"type": "http.request", "body": b"x" * 64, "more_body": True},
                {"type": "http.request", "body": b"x" * 65, "more_body": True},
                {"type": "http.request", "body": b"never read", "more_body": False},
            ]
            self.assertEqual(await self.request(chunks, extra), (413, 2))
        self.store.record.assert_not_called()

    async def test_timeout_disconnect_and_false_length_never_write(self):
        with patch("hosting.write.BODY_TIMEOUT_SECONDS", 0.001):
            self.assertEqual((await self.request(
                [{"type": "http.request", "body": b"", "more_body": True}], delay=0.05,
            ))[0], 408)
        self.assertEqual((await self.request([{"type": "http.disconnect"}]))[0], 400)
        self.assertEqual((await self.request(
            [{"type": "http.request", "body": b'{"action":"record"}', "more_body": False}],
            {"Content-Length": "1"},
        ))[0], 400)
        self.store.record.assert_not_called()

    async def test_invalid_origin_rejected_without_receiving_body(self):
        self.assertEqual(await self.request([], {"Origin": "null"}), (403, 0))


class TestHostingMarkerStore(unittest.TestCase):
    def setUp(self):
        self.pool = MagicMock()
        self.connection = self.pool.connection.return_value.__enter__.return_value
        self.connection.execute.return_value.fetchone.return_value = {"allowed": True}
        self.connection.execute.return_value.fetchall.return_value = [marker_row()]
        self.store = PostgresMarkerStore(self.pool, "test_writer")

    def test_record_is_transactional_idempotent_and_uses_only_server_key(self):
        expected = public_marker([marker_row()], owner_key(OWNER))
        self.assertEqual(self.store.record(OWNER), expected)
        self.assertEqual(self.store.record(OWNER), expected)
        commands = [call.args for call in self.connection.execute.call_args_list[:5]]
        self.assertEqual(commands, [
            ("SET TRANSACTION ISOLATION LEVEL READ COMMITTED",),
            (ROLE_CHECK, ("test_writer", POLICY_EXPRESSION, POLICY_EXPRESSION)),
            ("SELECT set_config('ffopt.actor', %s, true)", (owner_key(OWNER),)),
            (INSERT_MARKER, (owner_key(OWNER),)), (SELECT_MARKER, (owner_key(OWNER),)),
        ])
        self.assertEqual(self.connection.transaction.call_count, 2)
        self.assertNotIn("UPDATE", INSERT_MARKER)
        self.assertNotIn("created_at", INSERT_MARKER)
        self.assertNotIn(OWNER.subject, str(commands))
        self.pool.connection.assert_called_with(timeout=5)

    def test_status_and_readiness_have_no_insert(self):
        self.assertTrue(self.store.status(OWNER)["recorded"])
        self.store.check()
        for call in self.connection.execute.call_args_list:
            self.assertNotEqual(call.args[0], INSERT_MARKER)
        self.connection.execute.reset_mock()
        self.store.check()
        self.connection.execute.assert_called_once_with(
            ROLE_CHECK, ("test_writer", POLICY_EXPRESSION, POLICY_EXPRESSION),
        )

    def test_role_failure_prevents_actor_and_marker_operations(self):
        for row in (None, {"allowed": False}, {"allowed": None}):
            for method in (lambda: self.store.status(OWNER), lambda: self.store.record(OWNER), self.store.check):
                self.connection.execute.reset_mock()
                self.connection.execute.return_value.fetchone.return_value = row
                with self.assertRaises(DatabaseUnavailable) as raised:
                    method()
                self.assertEqual(raised.exception.code, "writer_boundary_invalid")
                self.assertNotIn(INSERT_MARKER, [call.args[0] for call in self.connection.execute.call_args_list])

    def test_corrupt_or_cross_owner_rows_are_not_disclosed(self):
        for rows in (
            [marker_row(), marker_row()], [marker_row(OTHER)],
            [{**marker_row(), "value": "private-unexpected-value"}],
            [{**marker_row(), "created_at": "private-unexpected-value"}],
            [{**marker_row(), "created_at": datetime(2026, 1, 1)}],
            [{**marker_row(), "private": "private-unexpected-value"}],
        ):
            self.connection.execute.return_value.fetchall.return_value = rows
            with self.assertRaises(DatabaseUnavailable) as raised:
                self.store.record(OWNER)
            self.assertEqual(raised.exception.code, "unexpected_marker")
            self.assertNotIn("private-unexpected", str(raised.exception))
        self.connection.execute.return_value.fetchall.return_value = []
        self.assertEqual(self.store.status(OWNER), {"recorded": False, "marker": None})
        with self.assertRaises(DatabaseUnavailable):
            self.store.record(OWNER)

    def test_pool_driver_and_commit_failures_are_sanitized(self):
        for error in (psycopg.OperationalError, PoolClosed, PoolTimeout, TooManyRequests):
            self.connection.execute.side_effect = error("private-test-detail")
            with self.assertRaises(DatabaseUnavailable) as raised:
                self.store.record(OWNER)
            self.assertEqual(raised.exception.code, "writer_operation_failed")
            self.assertNotIn("private-test-detail", str(raised.exception))
        self.connection.execute.side_effect = None
        self.connection.transaction.return_value.__exit__.side_effect = psycopg.OperationalError("private-commit-error")
        with self.assertRaises(DatabaseUnavailable):
            self.store.record(OWNER)

    def test_hash_is_stable_distinct_and_requires_identity(self):
        self.assertRegex(owner_key(OWNER), r"^[0-9a-f]{64}$")
        self.assertEqual(owner_key(OWNER), owner_key(Identity("google", OWNER.subject)))
        self.assertNotEqual(owner_key(OWNER), owner_key(OTHER))
        with self.assertRaises(ValueError):
            self.store.record({"provider": "google", "subject": OWNER.subject})
        self.pool.connection.assert_not_called()

    def test_writer_config_is_separate_bounded_tls_only_and_non_admin(self):
        values = {
            "FFOPT_WRITE_DB_HOST": "test-db.postgres.database.azure.com",
            "FFOPT_WRITE_DB_NAME": "hosting_stage", "FFOPT_WRITE_DB_USER": "ffopt_stage_probe_writer",
            "FFOPT_WRITE_DB_PASSWORD": "writer-test-only",
        }
        config = writer_settings("stage", {**values, "FFOPT_DB_PASSWORD": "reader-test-only"})
        self.assertNotIn("writer-test-only", repr(config))
        with patch("hosting.write_database.ConnectionPool") as pool:
            create_writer(config)
        options = pool.call_args.kwargs
        self.assertEqual(options["max_size"], 2)
        self.assertEqual(options["max_waiting"], 4)
        self.assertEqual(options["kwargs"]["sslmode"], "verify-full")
        self.assertEqual(options["kwargs"]["connect_timeout"], 5)
        self.assertIn("statement_timeout=3000", options["kwargs"]["options"])
        self.assertIn("lock_timeout=1000", options["kwargs"]["options"])
        self.assertFalse(options["open"])
        for overrides in (
            {"FFOPT_WRITE_DB_USER": "postgres"}, {"FFOPT_WRITE_DB_USER": "ffopt_stage_reader"},
            {"FFOPT_WRITE_DB_SSLMODE": "require"}, {"FFOPT_WRITE_DB_HOST": "127.0.0.1"},
            {"FFOPT_WRITE_DB_PASSWORD": "@Microsoft.KeyVault(SecretUri=unresolved)"},
            {"FFOPT_WRITE_DB_EXTRA": "no"}, {"FFOPT_WRITE_ENABLED": "true"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                writer_settings("stage", {**values, **overrides})

    def test_admin_migration_is_explicit_and_excluded_from_container_context(self):
        ignore = (ROOT / ".dockerignore").read_text()
        dockerfile = (ROOT / "hosting/Dockerfile").read_text()
        self.assertTrue(ignore.startswith("**\n"))
        self.assertIn("!hosting/\nhosting/**\n", ignore)
        self.assertIn("!hosting/static/\nhosting/static/**\n", ignore)
        self.assertNotIn("!hosting/db", ignore)
        for path in ("hosting/write.py", "hosting/write_database.py", "hosting/static/test-write.js",
                     "hosting/static/test-write.html"):
            self.assertIn("!" + path + "\n", ignore)
            self.assertIn(path, dockerfile)
        for path in ("hosting/db", "infra/", "tests/", "docs/"):
            self.assertNotIn(path, dockerfile)
        migration = (ROOT / "hosting/db/002_marker.sql").read_text()
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)
        self.assertNotIn("SECURITY DEFINER", migration)
        self.assertNotIn("hosting_probe.", migration)
        self.assertNotIn("ffopt_stage_reader", migration)


class TestHostingWriteScript(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for the dependency-free browser-script fixture.")
    def test_script_only_posts_after_click_and_never_inserts_html(self):
        fixture = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const requests = [];
const button = {disabled: true, addEventListener: (name, callback) => {button[name] = callback;}};
const status = {textContent: ""};
Object.defineProperty(status, "innerHTML", {set: () => {throw new Error("Unsafe HTML insertion");}});
let reply = {recorded: false, marker: null};
let statusCode = 200;
const context = {
  document: {getElementById: id => id === "record" ? button : status},
  fetch: async (url, options) => {
    requests.push({url, options});
    return {ok: statusCode === 200, status: statusCode, json: async () => reply};
  },
};
vm.runInNewContext(fs.readFileSync("hosting/static/test-write.js", "utf8"), context);
(async () => {
  await new Promise(setImmediate);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.method, "GET");
  assert.equal(requests[0].options.body, undefined);
  assert.match(status.textContent, /No synthetic marker/);
  reply = {recorded: true, marker: {value: "synthetic-write-v1", created_at: "2026-09-01T00:00:00Z"}};
  await button.click();
  const post = requests[1];
  assert.equal(post.url, "/fantasy-football/api/test-write");
  assert.equal(post.options.method, "POST");
  assert.equal(post.options.mode, "same-origin");
  assert.equal(post.options.credentials, "same-origin");
  assert.equal(post.options.referrerPolicy, "same-origin");
  assert.equal(post.options.redirect, "error");
  assert.equal(post.options.headers["Content-Type"], "application/json");
  assert.equal(post.options.headers["X-FFOPT-Write"], "record-v1");
  assert.equal(post.options.body, '{"action":"record"}');
  const original = status.textContent;
  await button.click();
  assert.equal(status.textContent, original);
  for (const code of [401, 403, 503]) {
    statusCode = code;
    await button.click();
    assert.doesNotMatch(status.textContent, /Synthetic marker recorded/);
  }
  statusCode = 200;
  reply = {recorded: true, marker: {value: "unexpected", created_at: "private"}};
  await button.click();
  assert.match(status.textContent, /Could not confirm/);
  assert.doesNotMatch(status.textContent, /private/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", fixture], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
