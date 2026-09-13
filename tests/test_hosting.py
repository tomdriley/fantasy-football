from contextlib import contextmanager
from email.message import Message
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient

from hosting.app import Settings, create_app
from scripts import check_hosted_app as smoke

SHA = "a" * 40
ROOT = Path(__file__).resolve().parent.parent


class TestHostingSettings(unittest.TestCase):
    def test_local_defaults_and_explicit_stage_config(self):
        self.assertEqual(Settings.from_environment({}), Settings())
        settings = Settings.from_environment({
            "FFOPT_HOSTING_ENVIRONMENT": "stage",
            "FFOPT_HOSTING_RELEASE": SHA,
            "FFOPT_HOSTING_ALLOWED_HOSTS": "STAGE.EXAMPLE.TEST",
        })
        self.assertEqual(settings.allowed_hosts, ("stage.example.test",))

    def test_stage_fails_closed_on_missing_or_invalid_configuration(self):
        cases = [
            {"FFOPT_HOSTING_ENVIRONMENT": "stage"},
            {"FFOPT_HOSTING_ENVIRONMENT": "stage", "FFOPT_HOSTING_ALLOWED_HOSTS": "stage.example.test"},
            {"FFOPT_HOSTING_ENVIRONMENT": "production"},
            {"FFOPT_HOSTING_ALLOWED_HOSTS": ""},
            {"FFOPT_HOSTING_ALLOWED_HOSTS": "*.example.test"},
            {"FFOPT_HOSTING_ALLOWED_HOSTS": "example.test:8080"},
            {"FFOPT_HOSTING_ALLOWED_HOSTS": "https://example.test"},
            {"FFOPT_HOSTING_RELEASE": "<script>"},
            {"FFOPT_HOSTING_RELEASE": "A" * 40},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Settings.from_environment(values)
        with self.assertRaises(ValueError):
            Settings(environment="stage", release=SHA)

    def test_settings_do_not_read_unrelated_secrets(self):
        values = {"UNRELATED_SECRET": "not-a-real-secret", "FFOPT_API_TOKEN": "not-a-real-token"}
        self.assertEqual(Settings.from_environment(values), Settings())


class TestHostingApp(unittest.TestCase):
    def setUp(self):
        self.app = create_app(Settings(environment="stage", release=SHA, allowed_hosts=("stage.example.test",)))
        self.client = TestClient(self.app, base_url="https://stage.example.test")
        self.addCleanup(self.client.close)

    def test_exact_health_readiness_and_release(self):
        for path, expected in (
            ("/healthz", {"status": "ok"}),
            ("/readyz", {"status": "ready"}),
            ("/fantasy-football/api/status", {
                "application": "fantasy-football-hosting", "environment": "stage",
                "release": SHA, "phase": "deployment",
            }),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), expected)
                self.assertEqual(self.client.head(path).content, b"")

    def test_page_and_redirect_use_prefix_without_forwarded_authority(self):
        page = self.client.get("/fantasy-football/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f"<code>{SHA}</code>", page.text)
        self.assertIn('href="/fantasy-football/api/status"', page.text)
        response = self.client.get(
            "/fantasy-football", follow_redirects=False,
            headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http"},
        )
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.headers["location"], "/fantasy-football/")

    def test_no_domain_endpoints_or_interactive_docs(self):
        for path in ("/api/v1/status", "/api/v1/advice", "/api/v1/snapshots", "/api/v1/jobs",
                     "/docs", "/openapi.json", "/.env", "/data/archive.sqlite3", "/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_methods_and_request_bodies_are_rejected(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
            with self.subTest(method=method):
                response = self.client.request(method, "/fantasy-football/api/status", json={"value": 1})
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["allow"], "GET, HEAD")
        for headers in ({"Content-Length": "1"}, {"Transfer-Encoding": "chunked"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get("/healthz", headers=headers).status_code, 413)

    def test_host_guard_and_security_headers_also_cover_errors(self):
        self.assertEqual(self.client.get("/healthz", headers={"Host": "evil.example"}).status_code, 400)
        for path in ("/healthz", "/fantasy-football/", "/missing"):
            response = self.client.get(path)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")
            self.assertIn("default-src 'none'", response.headers["content-security-policy"])
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
            self.assertEqual(response.headers["strict-transport-security"], "max-age=31536000")

    def test_local_does_not_set_hsts(self):
        with TestClient(create_app(Settings()), base_url="http://localhost") as client:
            self.assertNotIn("strict-transport-security", client.get("/healthz").headers)

    def test_factory_does_not_import_or_initialize_advisor(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; from hosting.app import create_app, Settings; create_app(Settings()); "
             "assert not any(n == 'ffopt' or n.startswith('ffopt.') for n in sys.modules)"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_unrelated_environment_values_in_public_responses(self):
        with patch.dict(os.environ, {"UNRELATED_SECRET": "private-test-value"}):
            for path in ("/fantasy-football/", "/fantasy-football/api/status"):
                self.assertNotIn("private-test-value", self.client.get(path).text)


class TestHostingSmoke(unittest.TestCase):
    def payloads(self):
        return {
            "/healthz": ("application/json", json.dumps({"status": "ok"}).encode()),
            "/readyz": ("application/json", json.dumps({"status": "ready"}).encode()),
            "/fantasy-football/api/status": ("application/json", json.dumps({
                "application": "fantasy-football-hosting", "environment": "stage",
                "release": SHA, "phase": "deployment",
            }).encode()),
            "/fantasy-football/": (
                "text/html",
                f'<main data-application="fantasy-football-hosting"><code>{SHA}</code>'
                '<a href="/fantasy-football/api/status">status</a></main>'.encode(),
            ),
        }

    @contextmanager
    def replies(self, payloads):
        @contextmanager
        def open_response(req, timeout):
            self.assertEqual(timeout, 10)
            self.assertNotIn("Authorization", req.headers)
            content_type, body = payloads[smoke.parse.urlsplit(req.full_url).path]
            response = io.BytesIO(body)
            response.status = 200
            response.headers = Message()
            response.headers["Content-Type"] = content_type
            response.headers["Cache-Control"] = "no-store"
            yield response

        with patch.object(smoke.request.OpenerDirector, "open", side_effect=open_response):
            yield

    def test_expected_release_passes(self):
        with self.replies(self.payloads()):
            smoke.check_release("https://stage.example.test", SHA, "stage")

    def database_payloads(self):
        payloads = self.payloads()
        status = json.loads(payloads["/fantasy-football/api/status"][1])
        status["phase"] = "database-readonly"
        payloads["/fantasy-football/api/status"] = ("application/json", json.dumps(status).encode())
        payloads["/fantasy-football/api/sample"] = ("application/json", json.dumps({
            "dataset": "hosting-probe-v1",
            "rows": [
                {"id": 1, "label": "synthetic-alpha", "value": 10},
                {"id": 2, "label": "synthetic-beta", "value": 20},
                {"id": 3, "label": "synthetic-gamma", "value": 30},
            ],
        }).encode())
        kind, page = payloads["/fantasy-football/"]
        payloads["/fantasy-football/"] = (kind, page + b'<a href="/fantasy-football/api/sample">sample</a>')
        return payloads

    def test_database_phase_checks_sample_and_page(self):
        with self.replies(self.database_payloads()):
            smoke.check_release("https://stage.example.test", SHA, "stage", expected_phase="database-readonly")
        for path in ("/fantasy-football/api/status", "/fantasy-football/"):
            payloads = self.database_payloads()
            payloads[path] = self.payloads()[path]
            with self.subTest(path=path), self.replies(payloads), self.assertRaises(smoke.CheckFailed):
                smoke.check_release("https://stage.example.test", SHA, "stage", expected_phase="database-readonly")
        with self.replies(self.database_payloads()), self.assertRaises(smoke.CheckFailed):
            smoke.check_release("https://stage.example.test", SHA, "stage")

    def test_database_sample_requires_exact_shape_and_values(self):
        expected = json.loads(self.database_payloads()["/fantasy-football/api/sample"][1])
        bad_samples = [
            {}, {**expected, "dataset": "other"}, {**expected, "extra": True},
            {**expected, "rows": expected["rows"][:-1]},
            {**expected, "rows": list(reversed(expected["rows"]))},
        ]
        for key, value in (("id", True), ("id", 1.0), ("value", 11),
                           ("label", "wrong"), ("extra", "field")):
            rows = [dict(row) for row in expected["rows"]]
            rows[0][key] = value
            bad_samples.append({**expected, "rows": rows})
        for sample in bad_samples:
            payloads = self.database_payloads()
            payloads["/fantasy-football/api/sample"] = ("application/json", json.dumps(sample).encode())
            with self.subTest(sample=sample), self.replies(payloads), self.assertRaises(smoke.CheckFailed):
                smoke.check_release("https://stage.example.test", SHA, "stage", expected_phase="database-readonly")

    def test_database_readiness_and_sample_http_failures_are_rejected(self):
        for path in ("/readyz", "/fantasy-football/api/sample"):
            with self.subTest(path=path), self.replies(self.database_payloads()):
                original_open = smoke.request.OpenerDirector.open

                def open_response(opener, req, timeout):
                    if req.full_url.endswith(path):
                        raise HTTPError(req.full_url, 503, "Unavailable", {}, None)
                    return original_open(req, timeout=timeout)

                with patch.object(smoke.request.OpenerDirector, "open", new=open_response), \
                        self.assertRaisesRegex(smoke.CheckFailed, "HTTP 503"):
                    smoke.check_release("https://stage.example.test", SHA, "stage", expected_phase="database-readonly")

    def test_cli_phase_default_and_selection(self):
        for phase in ("deployment", "database-readonly"):
            args = ["https://stage.example.test", "--expected-release", SHA]
            if phase != "deployment":
                args += ["--expected-phase", phase]
            with patch.object(smoke, "check_release") as check, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(smoke.main(args), 0)
            check.assert_called_once_with(
                "https://stage.example.test", SHA, "stage", allow_http=False, expected_phase=phase,
            )
        with patch.object(smoke.request, "build_opener") as opener, self.assertRaises(smoke.CheckFailed):
            smoke.check_release("https://stage.example.test", SHA, "stage", expected_phase="invalid")
        opener.assert_not_called()
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            smoke.main(["https://stage.example.test", "--expected-release", SHA, "--expected-phase", "invalid"])

    def test_wrong_application_release_readiness_and_extra_fields_fail(self):
        for key, value in (("application", "article-service"), ("release", "b" * 40),
                           ("environment", "local"), ("unexpected", "field")):
            payloads = self.payloads()
            status = json.loads(payloads["/fantasy-football/api/status"][1])
            status[key] = value
            payloads["/fantasy-football/api/status"] = ("application/json", json.dumps(status).encode())
            with self.subTest(key=key), self.replies(payloads), self.assertRaises(smoke.CheckFailed):
                smoke.check_release("https://stage.example.test", SHA, "stage")
        payloads = self.payloads()
        payloads["/readyz"] = ("application/json", b'{"status":"not ready"}')
        with self.replies(payloads), self.assertRaises(smoke.CheckFailed):
            smoke.check_release("https://stage.example.test", SHA, "stage")

    def test_invalid_json_content_types_sizes_and_page_fail(self):
        cases = [
            ("/healthz", "text/html", b"ok"),
            ("/healthz", "application/json", b"not json"),
            ("/healthz", "application/json", b"x" * (smoke.MAX_RESPONSE_BYTES + 1)),
            ("/fantasy-football/", "text/html", b"wrong app"),
        ]
        for path, kind, body in cases:
            payloads = self.payloads()
            payloads[path] = (kind, body)
            with self.subTest(path=path, kind=kind), self.replies(payloads), self.assertRaises(smoke.CheckFailed):
                smoke.check_release("https://stage.example.test", SHA, "stage")

    def test_credentials_query_paths_and_remote_http_are_rejected(self):
        for value in (
            "https://user:password@example.test", "https://example.test/?token=secret",
            "https://example.test/fantasy-football/", "https://example.test/#fragment",
            "https://example.test:bad", "http://example.test", "file:///tmp/test",
        ):
            with self.subTest(value=value), self.assertRaises(smoke.CheckFailed):
                smoke.origin(value, allow_http=True)
        self.assertEqual(smoke.origin("http://127.0.0.1:8788/", allow_http=True), "http://127.0.0.1:8788")
        with self.assertRaises(smoke.CheckFailed):
            smoke.origin("http://localhost")

    def test_redirect_tls_error_and_body_details_are_not_accepted(self):
        for exc in (
            HTTPError("https://stage.example.test", 302, "redirect", {}, None),
            URLError("private-transport-detail"),
        ):
            with patch.object(smoke.request.OpenerDirector, "open", side_effect=exc):
                with self.assertRaises(smoke.CheckFailed) as raised:
                    smoke.check_release("https://stage.example.test", SHA, "stage")
                self.assertNotIn("private-transport-detail", str(raised.exception))
        self.assertIsNone(smoke.NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.test"))

    def test_cli_reports_failure_and_only_bounded_retries(self):
        with patch.object(smoke, "check_release", side_effect=smoke.CheckFailed("wrong release")), \
                patch.object(smoke.time, "sleep") as sleep, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = smoke.main(["https://stage.example.test", "--expected-release", SHA, "--attempts", "2"])
        self.assertEqual(code, 1)
        sleep.assert_called_once_with(5)
        self.assertIn("2/2 failed", stderr.getvalue())
