#!/usr/bin/env python3
"""Check a hosting release without credentials, redirects, or real fantasy data."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import time
from urllib import error, parse, request

MAX_RESPONSE_BYTES = 65536


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
        raise CheckFailed(f"{parse.urlsplit(url).path}: HTTP {exc.code}; redirects are not accepted.") from exc
    except (error.URLError, OSError) as exc:
        raise CheckFailed(f"{parse.urlsplit(url).path}: connection or TLS check failed.") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise CheckFailed("Response exceeds the smoke-check size limit.")
    return body


def check_release(url: str, release: str, environment: str, *, allow_http: bool = False):
    base = origin(url, allow_http=allow_http)
    if not re.fullmatch(r"(?:[0-9a-f]{40}|development)", release):
        raise CheckFailed("Expected release must be a lowercase commit SHA or development.")
    if environment not in ("local", "stage") or (environment == "stage" and release == "development"):
        raise CheckFailed("Use local or stage; stage requires a commit SHA.")
    opener = request.build_opener(NoRedirects)
    expected = {
        "/healthz": {"status": "ok"},
        "/readyz": {"status": "ready"},
        "/fantasy-football/api/status": {
            "application": "fantasy-football-hosting",
            "environment": environment,
            "release": release,
            "phase": "deployment",
        },
    }
    for path, value in expected.items():
        raw = fetch(opener, base + path, "application/json")
        try:
            actual = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise CheckFailed(f"{path}: invalid JSON response.") from exc
        if actual != value:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="app origin, e.g. https://the-stage-host.azurewebsites.net")
    parser.add_argument("--expected-release", required=True)
    parser.add_argument("--expected-environment", choices=("local", "stage"), default="stage")
    parser.add_argument("--allow-http", action="store_true", help="permit HTTP for loopback only")
    parser.add_argument("--attempts", type=int, default=1, choices=range(1, 61), metavar="1..60")
    args = parser.parse_args(argv)
    for attempt in range(args.attempts):
        try:
            check_release(
                args.url, args.expected_release, args.expected_environment, allow_http=args.allow_http,
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
