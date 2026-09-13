import io
from email.message import Message
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from hosting.app import Settings, create_app
from hosting.auth import (
    AUTH_PHASE, Identity, PUBLIC_PATHS, SESSION_PATH, SIGN_IN_URL, parse_allowlist,
    platform_identity,
)
from hosting.database import PostgresSampleReader
from hosting.sample import SAMPLE_ROWS, public_sample
from scripts import check_hosted_app as smoke

SHA = "a" * 40
HOST = "stage.example.test"
OWNER = Identity("google", "100000000000000000001")
OTHER = Identity("google", "100000000000000000002")


def headers(identity=OWNER):
    return {
        "X-MS-CLIENT-PRINCIPAL-IDP": identity.provider,
        "X-MS-CLIENT-PRINCIPAL-ID": identity.subject,
        "X-MS-CLIENT-PRINCIPAL-NAME": "email-does-not-authorize@example.test",
        "X-MS-TOKEN-GOOGLE-ID-TOKEN": "not-a-real-token",
        "X-MS-CLIENT-PRINCIPAL": "ignored-claim-payload",
    }


class TestHostingIdentity(unittest.TestCase):
    def test_allowlist_is_immutable_provider_and_subject_not_email(self):
        self.assertEqual(parse_allowlist("[]"), frozenset())
        self.assertEqual(parse_allowlist(json.dumps([
            {"provider": "google", "subject": OWNER.subject},
            {"provider": "google", "subject": OWNER.subject},
        ])), frozenset({OWNER}))
        cases = [
            "", "{}", "null", "1", '"google:subject"', "[null]",
            json.dumps([{"provider": "aad", "subject": OWNER.subject}]),
            json.dumps([{"provider": "Google", "subject": OWNER.subject}]),
            json.dumps([{"provider": "google", "subject": "person@example.test"}]),
            json.dumps([{"provider": "google", "subject": 123}]),
            json.dumps([{"provider": "google", "subject": "x", "email": "x"}]),
            json.dumps([{"provider": "google", "subject": "x"}] * 33),
            '[{"provider":"aad","provider":"google","subject":"x"}]',
            "[" * 1200, " " * 16385,
        ]
        for value in cases:
            with self.subTest(value=value[:80]), self.assertRaises(ValueError):
                parse_allowlist(value)

    def test_only_two_bounded_single_platform_identity_headers_are_used(self):
        valid = [(name.lower().encode(), value.encode()) for name, value in headers().items()]
        self.assertEqual(platform_identity({"headers": valid}), OWNER)
        self.assertEqual(platform_identity({"headers": [
            (b"x-ms-client-principal-idp", b"google"),
            (b"x-ms-client-principal-id", b"sid:opaque-provider-id="),
        ]}), Identity("google", "sid:opaque-provider-id="))
        for value in (b"", b"x" * 256, b"x,y", b"x y", b"person@example.test", b"\xff", b"x\n"):
            with self.subTest(value=value):
                self.assertIsNone(platform_identity({"headers": [
                    (b"x-ms-client-principal-idp", b"google"),
                    (b"x-ms-client-principal-id", value),
                ]}))
        for values in (
            [], valid[1:], [(b"x-ms-client-principal-idp", b"google")],
            [(b"x-ms-client-principal-idp", b"aad"), (b"x-ms-client-principal-id", OWNER.subject.encode())],
            [(b"x-ms-client-principal-idp", b"Google"), (b"x-ms-client-principal-id", OWNER.subject.encode())],
            valid + [(b"X-MS-CLIENT-PRINCIPAL-ID", OWNER.subject.encode())],
            valid + [(b"X-MS-CLIENT-PRINCIPAL-IDP", b"google")],
        ):
            with self.subTest(values=values):
                self.assertIsNone(platform_identity({"headers": values}))

    def test_settings_never_enable_platform_trust_in_local_environment(self):
        with self.assertRaises(ValueError):
            Settings(phase=AUTH_PHASE)
        with self.assertRaises(ValueError):
            Settings(auth_allowlist=frozenset({OWNER}))
        with self.assertRaises(ValueError):
            Settings(auth_allowlist={OWNER})
        settings = Settings.from_environment({
            "FFOPT_HOSTING_ENVIRONMENT": "stage", "FFOPT_HOSTING_RELEASE": SHA,
            "FFOPT_HOSTING_ALLOWED_HOSTS": HOST, "FFOPT_HOSTING_PHASE": AUTH_PHASE,
        })
        self.assertEqual(settings.auth_allowlist, frozenset())
        for key, value in (
            ("FFOPT_AUTH_ALLOWED_IDENTITIES", "[]"),
            ("GOOGLE_PROVIDER_AUTHENTICATION_SECRET", "@Microsoft.KeyVault(SecretUri=not-real)"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                Settings.from_environment({"FFOPT_HOSTING_PHASE": "database-readonly", key: value})


class TestHostingAuthentication(unittest.TestCase):
    def setUp(self):
        self.reader = MagicMock(spec=PostgresSampleReader)
        self.reader.sample.return_value = public_sample(SAMPLE_ROWS)
        self.settings = Settings(
            environment="stage", release=SHA, allowed_hosts=(HOST,), phase=AUTH_PHASE,
            auth_allowlist=frozenset({OWNER}),
        )
        self.client = TestClient(create_app(self.settings, reader=self.reader), base_url=f"https://{HOST}")
        self.addCleanup(self.client.close)

    def test_anonymous_can_only_see_technical_public_paths(self):
        for path in PUBLIC_PATHS:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertNotIn(OWNER.subject, response.text)
            self.assertNotIn("synthetic-alpha", response.text)
            self.assertNotIn("not-a-real-token", response.text)
        page = self.client.get("/fantasy-football/").text
        self.assertIn(f'href="{SIGN_IN_URL}"', page)
        for path in (SESSION_PATH, "/fantasy-football/api/sample", "/docs", "/future-api"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                response = self.client.head(path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.content, b"")

    def test_bootstrap_shows_only_own_identity_and_never_reads_sample(self):
        response = self.client.get(SESSION_PATH, headers=headers(OTHER))
        self.assertEqual(response.json(), {
            "authenticated": True, "authorized": False,
            "identity": {"provider": "google", "subject": OTHER.subject},
        })
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn(OWNER.subject, response.text)
        self.assertNotIn("email-does-not-authorize", response.text)
        for path in ("/fantasy-football/api/sample", "/future-api"):
            self.assertEqual(self.client.get(path, headers=headers(OTHER)).status_code, 403)
        self.reader.sample.assert_not_called()

    def test_allowlisted_subject_can_read_only_the_synthetic_sample(self):
        response = self.client.get(SESSION_PATH, headers=headers())
        self.assertTrue(response.json()["authorized"])
        response = self.client.get("/fantasy-football/api/sample", headers=headers())
        self.assertEqual(response.json(), public_sample(SAMPLE_ROWS))
        self.reader.sample.assert_called_once_with()
        self.assertEqual(self.client.get("/api/v1/advice", headers=headers()).status_code, 404)

    def test_empty_allowlist_does_not_authorize_any_signed_in_user(self):
        settings = Settings(
            environment="stage", release=SHA, allowed_hosts=(HOST,), phase=AUTH_PHASE,
        )
        with TestClient(create_app(settings, reader=self.reader), base_url=f"https://{HOST}") as client:
            self.assertFalse(client.get(SESSION_PATH, headers=headers()).json()["authorized"])
            self.assertEqual(client.get("/fantasy-football/api/sample", headers=headers()).status_code, 403)
        self.reader.sample.assert_not_called()

    def test_all_application_writes_and_bodies_remain_disallowed(self):
        for identity_headers in ({}, headers(), headers(OTHER)):
            for path in (SESSION_PATH, "/fantasy-football/api/sample", "/future-write"):
                for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
                    response = self.client.request(method, path, headers=identity_headers)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response.headers["allow"], "GET, HEAD")
            self.assertEqual(self.client.get(
                SESSION_PATH, headers={**identity_headers, "Content-Length": "1"},
            ).status_code, 413)
        self.reader.sample.assert_not_called()

    def test_provider_mismatch_and_email_do_not_authorize(self):
        for overrides in (
            {"X-MS-CLIENT-PRINCIPAL-IDP": "aad"},
            {"X-MS-CLIENT-PRINCIPAL-ID": ""},
            {"X-MS-CLIENT-PRINCIPAL-ID": "email-does-not-authorize@example.test"},
        ):
            response = self.client.get(SESSION_PATH, headers={**headers(), **overrides})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"error": "authentication_required"})
        for identity_headers, code in (({}, 401), (headers(OTHER), 403)):
            response = self.client.get("/fantasy-football/api/sample", headers=identity_headers)
            self.assertEqual(response.status_code, code)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_local_and_legacy_phases_ignore_forged_identity_headers(self):
        for environment, phase in (
            ("local", "deployment"), ("local", "database-readonly"),
            ("stage", "deployment"), ("stage", "database-readonly"),
        ):
            settings = Settings(
                environment=environment, release=SHA, allowed_hosts=(HOST,), phase=phase,
            )
            app = create_app(settings, reader=self.reader if phase == "database-readonly" else None)
            with TestClient(app, base_url=f"https://{HOST}") as client, \
                    patch("hosting.auth.platform_identity") as parse:
                self.assertEqual(client.get(SESSION_PATH, headers=headers()).status_code, 404)
                sample = client.get("/fantasy-football/api/sample", headers=headers())
                self.assertEqual(sample.status_code, 200 if phase == "database-readonly" else 404)
                parse.assert_not_called()


class TestHostingAuthenticationSmoke(unittest.TestCase):
    def test_complete_auth_smoke_requires_platform_to_strip_forged_headers(self):
        reader = MagicMock(spec=PostgresSampleReader)
        reader.sample.return_value = public_sample(SAMPLE_ROWS)
        settings = Settings(
            environment="stage", release=SHA, allowed_hosts=(HOST,), phase=AUTH_PHASE,
        )
        for platform_strips_headers in (False, True):
            with self.subTest(platform_strips_headers=platform_strips_headers), \
                    TestClient(create_app(settings, reader=reader), base_url=f"https://{HOST}") as client:
                def open_response(req, timeout):
                    incoming = dict(req.headers)
                    if platform_strips_headers:
                        incoming = {key: value for key, value in incoming.items() if not key.lower().startswith("x-ms-")}
                    response = client.get(req.full_url, headers=incoming, follow_redirects=False)
                    body = io.BytesIO(response.content)
                    body.headers = Message()
                    for key, value in response.headers.items():
                        body.headers[key] = value
                    body.status = response.status_code
                    if response.status_code >= 400:
                        raise HTTPError(req.full_url, response.status_code, "", body.headers, body)
                    return body

                with patch.object(smoke.request.OpenerDirector, "open", side_effect=open_response):
                    if platform_strips_headers:
                        smoke.check_release(f"https://{HOST}", SHA, "stage", expected_phase=AUTH_PHASE)
                    else:
                        with self.assertRaises(smoke.CheckFailed):
                            smoke.check_release(f"https://{HOST}", SHA, "stage", expected_phase=AUTH_PHASE)

    def test_denial_check_accepts_only_401_and_does_not_read_error_bodies(self):
        for code in (200, 302, 400, 401, 403, 500):
            opener = MagicMock()
            body = io.BytesIO(b"must-not-display")
            opener.open.side_effect = HTTPError("https://stage.example.test", code, "private", {}, body)
            with self.subTest(code=code):
                if code == 401:
                    smoke.check_anonymous_denial(opener, "https://stage.example.test", "/protected")
                else:
                    with self.assertRaises(smoke.CheckFailed):
                        smoke.check_anonymous_denial(opener, "https://stage.example.test", "/protected")
            self.assertTrue(body.closed)

    def test_successful_protected_response_fails_smoke(self):
        opener = MagicMock()
        with self.assertRaises(smoke.CheckFailed):
            smoke.check_anonymous_denial(opener, "https://stage.example.test", "/protected")

    def test_forged_internet_headers_include_a_google_subject(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://stage.example.test", 401, "", {}, None)
        smoke.check_anonymous_denial(opener, "https://stage.example.test", SESSION_PATH, forged=True)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_header("X-ms-client-principal-idp"), "google")
        self.assertTrue(req.get_header("X-ms-client-principal-id"))
        self.assertIsNone(req.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
