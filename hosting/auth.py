"""Authorization for a stage container reachable only through App Service Easy Auth."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

AUTH_PHASE = "authentication-only"
WRITE_PHASE = "authenticated-write"
AUTH_PHASES = (AUTH_PHASE, WRITE_PHASE)
AUTH_CAPABILITY = "google-allowlist-v1"
WRITE_CAPABILITY = "synthetic-marker-v1"
PUBLIC_PATHS = frozenset({
    "/healthz", "/readyz", "/fantasy-football", "/fantasy-football/",
    "/fantasy-football/api/status",
})
SESSION_PATH = "/fantasy-football/api/session"
SIGN_IN_URL = "/.auth/login/google?post_login_redirect_uri=/fantasy-football/api/session"
SUBJECT_PATTERN = re.compile(r"[A-Za-z0-9._~:/+=-]{1,255}", re.ASCII)
MAX_ALLOWLIST_CHARS = 16384
MAX_IDENTITIES = 32


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str

    def __post_init__(self):
        if self.provider != "google" or not isinstance(self.subject, str) or not SUBJECT_PATTERN.fullmatch(self.subject):
            raise ValueError("An identity must use google and a bounded, non-email provider subject.")


def parse_allowlist(value: str) -> frozenset[Identity]:
    if len(value) > MAX_ALLOWLIST_CHARS:
        raise ValueError("Authentication allowlist is too large.")
    try:
        entries = json.loads(value, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise ValueError("Authentication allowlist must be a JSON array of provider/subject objects.") from exc
    if not isinstance(entries, list) or len(entries) > MAX_IDENTITIES:
        raise ValueError("Authentication allowlist must be a bounded JSON array.")
    identities = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"provider", "subject"}:
            raise ValueError("Authentication allowlist entries require only provider and subject.")
        identities.add(Identity(entry["provider"], entry["subject"]))
    return frozenset(identities)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Authentication allowlist objects must not repeat keys.")
        result[key] = value
    return result


def platform_identity(scope: Scope) -> Identity | None:
    # These two documented Easy Auth headers avoid decoding or exposing the
    # claims/token payload. They are NOT credentials on an unprotected origin.
    values = {}
    for name, value in scope.get("headers", []):
        name = name.lower()
        if name not in (b"x-ms-client-principal-idp", b"x-ms-client-principal-id"):
            continue
        if name in values or len(value) > 255:
            return None
        values[name] = value
    if values.get(b"x-ms-client-principal-idp") != b"google":
        return None
    try:
        subject = values.get(b"x-ms-client-principal-id", b"").decode("ascii")
        return Identity("google", subject)
    except (UnicodeDecodeError, ValueError):
        return None


class GoogleAuthorization:
    def __init__(self, app: ASGIApp, *, allowlist: frozenset[Identity]):
        self.app = app
        self.allowlist = allowlist

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            return await self.app(scope, receive, send)
        identity = platform_identity(scope)
        if identity is None:
            response = JSONResponse({"error": "authentication_required"}, status_code=401)
            return await response(scope, receive, send)
        authorized = identity in self.allowlist
        if scope["path"] != SESSION_PATH and not authorized:
            response = JSONResponse({"error": "not_authorized"}, status_code=403)
            return await response(scope, receive, send)
        scope.setdefault("state", {})["hosting_identity"] = identity
        scope["state"]["hosting_authorized"] = authorized
        await self.app(scope, receive, send)
