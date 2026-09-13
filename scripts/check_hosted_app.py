#!/usr/bin/env python3
"""Check a hosting release without credentials, redirects, or real fantasy data."""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import re
import sys
import time
from urllib import error, parse, request

MAX_RESPONSE_BYTES = 65536
AUTH_PHASE = "authentication-only"
WRITE_PHASE = "authenticated-write"
AUTH_PHASES = (AUTH_PHASE, WRITE_PHASE)


class CheckFailed(ValueError):
    pass


class NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def origin(value: str, *, allow_http: bool = False) -> str:
    try:
        parts = parse.urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise CheckFailed("URL has an invalid host or port.") from exc
    if (
        not parts.hostname or parts.username is not None or parts.password is not None
        or parts.path not in ("", "/") or parts.query or parts.fragment
        or parts.scheme not in ("http", "https") or (port is not None and not 1 <= port <= 65535)
    ):
        raise CheckFailed("Use an HTTP(S) origin without credentials, path, query, or fragment.")
    if parts.scheme != "https":
        try:
            local = ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            local = parts.hostname == "localhost"
        if not allow_http or not local:
            raise CheckFailed("HTTPS is required; --allow-http is only for local loopback checks.")
    return parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def fetch(opener: request.OpenerDirector, url: str, content_type: str) -> bytes:
    try:
        with opener.open(request.Request(url, headers={"Accept": content_type}), timeout=10) as response:
            if response.status != 200:
                raise CheckFailed(f"{parse.urlsplit(url).path}: expected HTTP 200.")
            if response.headers.get_content_type() != content_type:
                raise CheckFailed(f"{parse.urlsplit(url).path}: unexpected response content type.")
            if response.headers.get("Cache-Control") != "no-store":
                raise CheckFailed(f"{parse.urlsplit(url).path}: missing no-store response policy.")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        exc.close()
        raise CheckFailed(f"{parse.urlsplit(url).path}: HTTP {exc.code}; redirects are not accepted.") from exc
    except (error.URLError, OSError) as exc:
        raise CheckFailed(f"{parse.urlsplit(url).path}: connection or TLS check failed.") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise CheckFailed("Response exceeds the smoke-check size limit.")
    return body


def check_anonymous_denial(opener, base: str, path: str, *, forged: bool = False, method: str = "GET"):
    headers = {"Accept": "application/json"}
    if forged:
        subject = "100000000000000000000"
        headers.update({
            "X-MS-CLIENT-PRINCIPAL-IDP": "google",
            "X-MS-CLIENT-PRINCIPAL-ID": subject,
            "X-MS-CLIENT-PRINCIPAL-NAME": "forged@example.invalid",
            "X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps({
                "auth_typ": "google", "claims": [{"typ": "sub", "val": subject}],
                "name_typ": "name", "role_typ": "role",
            }).encode()).decode("ascii"),
        })
    data = None
    if method == "POST":
        headers.update({"Origin": base, "Content-Type": "application/json", "X-FFOPT-Write": "record-v1"})
        data = b'{"action":"record"}'
    try:
        with opener.open(request.Request(base + path, headers=headers, method=method, data=data), timeout=10):
            raise CheckFailed(f"{path}: unauthenticated request was not denied.")
    except error.HTTPError as exc:
        exc.close()
        if exc.code != 401:
            raise CheckFailed(f"{path}: expected HTTP 401 without credentials; redirects are not accepted.") from exc
    except (error.URLError, OSError) as exc:
        raise CheckFailed(f"{path}: connection or TLS check failed.") from exc


def check_release(
    url: str, release: str, environment: str, *,
    allow_http: bool = False, expected_phase: str = "deployment",
):
    base = origin(url, allow_http=allow_http)
    if not re.fullmatch(r"(?:[0-9a-f]{40}|development)", release):
        raise CheckFailed("Expected release must be a lowercase commit SHA or development.")
    if environment not in ("local", "stage") or (environment == "stage" and release == "development"):
        raise CheckFailed("Use local or stage; stage requires a commit SHA.")
    if expected_phase not in ("deployment", "database-readonly", *AUTH_PHASES):
        raise CheckFailed("Invalid expected hosting phase.")
    if expected_phase in AUTH_PHASES and (environment != "stage" or allow_http):
        raise CheckFailed("Authentication checks require the HTTPS stage Easy Auth origin.")
    opener = request.build_opener(NoRedirects)
    expected = {
        "/healthz": {"status": "ok"},
        "/readyz": {"status": "ready"},
        "/fantasy-football/api/status": {
            "application": "fantasy-football-hosting",
            "environment": environment,
            "release": release,
            "phase": expected_phase,
        },
    }
    if expected_phase == "database-readonly":
        expected["/fantasy-football/api/sample"] = {
            "dataset": "hosting-probe-v1",
            "rows": [
                {"id": 1, "label": "synthetic-alpha", "value": 10},
                {"id": 2, "label": "synthetic-beta", "value": 20},
                {"id": 3, "label": "synthetic-gamma", "value": 30},
            ],
        }
    for path, value in expected.items():
        raw = fetch(opener, base + path, "application/json")
        try:
            actual = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise CheckFailed(f"{path}: invalid JSON response.") from exc
        if json.dumps(actual, sort_keys=True) != json.dumps(value, sort_keys=True):
            raise CheckFailed(f"{path}: response does not identify the expected deployment.")
    try:
        page = fetch(opener, base + "/fantasy-football/", "text/html").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CheckFailed("Page is not valid UTF-8.") from exc
    for marker in (
        'data-application="fantasy-football-hosting"',
        f"<code>{release}</code>",
        'href="/fantasy-football/api/status"',
    ):
        if marker not in page:
            raise CheckFailed("Page does not identify the expected deployment or prefix.")
    if expected_phase == "database-readonly" and 'href="/fantasy-football/api/sample"' not in page:
        raise CheckFailed("Page does not link to the database sample.")
    if expected_phase in AUTH_PHASES:
        if 'href="/.auth/login/google?post_login_redirect_uri=/fantasy-football/api/session"' not in page:
            raise CheckFailed("Page does not link to managed Google sign-in.")
        protected = ["/fantasy-football/api/session", "/fantasy-football/api/sample"]
        if expected_phase == WRITE_PHASE:
            protected += [
                "/fantasy-football/api/test-write", "/fantasy-football/test-write",
                "/fantasy-football/static/test-write.js",
            ]
            if 'href="/fantasy-football/test-write"' not in page:
                raise CheckFailed("Page does not link to the protected write probe.")
        for path in protected:
            check_anonymous_denial(opener, base, path)
            check_anonymous_denial(opener, base, path, forged=True)
        if expected_phase == WRITE_PHASE:
            for forged in (False, True):
                check_anonymous_denial(opener, base, "/fantasy-football/api/test-write", forged=forged, method="POST")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="app origin, e.g. https://the-stage-host.azurewebsites.net")
    parser.add_argument("--expected-release", required=True)
    parser.add_argument("--expected-environment", choices=("local", "stage"), default="stage")
    parser.add_argument("--expected-phase", choices=("deployment", "database-readonly", *AUTH_PHASES), default="deployment")
    parser.add_argument("--allow-http", action="store_true", help="permit HTTP for loopback only")
    parser.add_argument("--attempts", type=int, default=1, choices=range(1, 61), metavar="1..60")
    args = parser.parse_args(argv)
    for attempt in range(args.attempts):
        try:
            check_release(
                args.url, args.expected_release, args.expected_environment, allow_http=args.allow_http,
                expected_phase=args.expected_phase,
            )
        except CheckFailed as exc:
            print(f"Hosting check {attempt + 1}/{args.attempts} failed: {exc}", file=sys.stderr)
            if attempt + 1 == args.attempts:
                return 1
            time.sleep(5)
        else:
            print(f"Hosting check passed: {args.expected_environment} release {args.expected_release}")
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
