"""A read-only hosting probe; database access is an explicit synthetic-only phase."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import asynccontextmanager
import html
import logging
import os
from pathlib import Path
import re
from string import Template
from typing import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from .database import DatabaseSettings, DatabaseUnavailable, PostgresSampleReader, create_reader
from .auth import AUTH_PHASE, GoogleAuthorization, Identity, SESSION_PATH, SIGN_IN_URL, parse_allowlist

LOG = logging.getLogger(__name__)
BASE_PATH = "/fantasy-football"
APPLICATION = "fantasy-football-hosting"
RELEASE_PATTERN = re.compile(r"(?:[0-9a-f]{40}|development)")
HOST_PATTERN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
)


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    release: str = "development"
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    phase: str = "deployment"
    auth_allowlist: frozenset[Identity] = frozenset()

    def __post_init__(self):
        if self.environment not in ("local", "stage"):
            raise ValueError("hosting environment must be local or stage")
        if self.phase not in ("deployment", "database-readonly", AUTH_PHASE):
            raise ValueError("hosting phase must be deployment, database-readonly or authentication-only")
        if self.phase == AUTH_PHASE and self.environment != "stage":
            raise ValueError("authentication-only trusts only the stage Easy Auth origin")
        if not isinstance(self.auth_allowlist, frozenset) or any(
            not isinstance(identity, Identity) for identity in self.auth_allowlist
        ):
            raise ValueError("authentication allowlist must be immutable provider/subject identities")
        if self.auth_allowlist and self.phase != AUTH_PHASE:
            raise ValueError("authentication allowlist requires the authentication-only phase")
        if not RELEASE_PATTERN.fullmatch(self.release):
            raise ValueError("hosting release must be a lowercase commit SHA or development")
        if self.environment == "stage" and self.release == "development":
            raise ValueError("stage requires a commit SHA release")
        if not self.allowed_hosts or any(
            len(host) > 253 or not HOST_PATTERN.fullmatch(host)
            for host in self.allowed_hosts
        ):
            raise ValueError("configure explicit DNS names or IPv4 hosts, without ports or wildcards")
        if self.environment == "stage" and any(
            host in ("localhost", "127.0.0.1", "testserver")
            for host in self.allowed_hosts
        ):
            raise ValueError("stage requires its real hostname, not local test hosts")

    @classmethod
    def from_environment(cls, values: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if values is None else values
        environment = values.get("FFOPT_HOSTING_ENVIRONMENT", "local")
        phase = values.get("FFOPT_HOSTING_PHASE", "deployment")
        if phase != AUTH_PHASE and any(
            key in values for key in ("FFOPT_AUTH_ALLOWED_IDENTITIES", "GOOGLE_PROVIDER_AUTHENTICATION_SECRET")
        ):
            raise ValueError("Authentication settings cannot be used with a legacy hosting phase.")
        hosts = values.get("FFOPT_HOSTING_ALLOWED_HOSTS")
        if hosts is None and environment == "stage":
            raise ValueError("FFOPT_HOSTING_ALLOWED_HOSTS is required in stage")
        return cls(
            environment=environment,
            release=values.get("FFOPT_HOSTING_RELEASE", "development"),
            allowed_hosts=(
                tuple(host.strip().lower() for host in hosts.split(","))
                if hosts is not None else ("localhost", "127.0.0.1")
            ),
            phase=phase,
            auth_allowlist=parse_allowlist(values.get("FFOPT_AUTH_ALLOWED_IDENTITIES", "[]")),
        )


class ReadOnlyBoundary:
    def __init__(self, app: ASGIApp, *, stage: bool):
        self.app = app
        self.headers = (
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"content-security-policy",
             b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
        )
        if stage:
            self.headers += ((b"strict-transport-security", b"max-age=31536000"),)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *self.headers]}
            await send(message)

        if scope["method"] not in ("GET", "HEAD"):
            response = JSONResponse(
                {"error": "This deployment probe has no write operations."},
                status_code=405, headers={"Allow": "GET, HEAD"},
            )
            return await response(scope, receive, secure_send)
        # No endpoint consumes a body. Reject advertised bodies without buffering.
        if any(
            key.lower() == b"transfer-encoding"
            or (key.lower() == b"content-length" and value != b"0")
            for key, value in scope.get("headers", [])
        ):
            response = JSONResponse(
                {"error": "Request bodies are not supported."}, status_code=413,
            )
            return await response(scope, receive, secure_send)
        await self.app(scope, receive, secure_send)


def create_app(
    settings: Settings | None = None, *, reader: PostgresSampleReader | None = None,
) -> FastAPI:
    settings = settings if settings is not None else Settings.from_environment()
    if settings.phase in ("database-readonly", AUTH_PHASE):
        reader = reader if reader is not None else create_reader(DatabaseSettings.from_environment(settings.environment))
    elif reader is not None or any(name.startswith("FFOPT_DB_") for name in os.environ):
        raise ValueError("Database settings require the database-readonly or authentication-only phase.")
    description = (
        "Google sign-in identifies your account; only operator-allowlisted provider subjects "
        "may read the synthetic sample. No application writes, advisor, or real league data are enabled."
        if settings.phase == AUTH_PHASE else
        "This checkpoint reads only approved synthetic PostgreSQL data. "
        "No login, application writes, background jobs, or real league data are enabled."
        if reader is not None else
        "No database, login, background jobs, or real league data are connected."
    )
    page = Template(
        (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    ).substitute(
        application=APPLICATION,
        environment=html.escape(settings.environment),
        release=html.escape(settings.release),
        status_url=f"{BASE_PATH}/api/status",
        phase_description=html.escape(description),
        database_link=(
            f'<p><a href="{BASE_PATH}/api/sample">Read the synthetic database sample</a></p>'
            if reader is not None else ""
        ),
        auth_link=(
            f'<p><a href="{html.escape(SIGN_IN_URL, quote=True)}">Sign in with Google</a></p>'
            f'<p><a href="{SESSION_PATH}">Check your own sign-in and access</a></p>'
            if settings.phase == AUTH_PHASE else ""
        ),
    )

    @asynccontextmanager
    async def lifespan(app):
        if reader is not None:
            reader.open()
        try:
            yield
        finally:
            if reader is not None:
                reader.close()

    app = FastAPI(
        title="Hosting foundation", docs_url=None, redoc_url=None, openapi_url=None,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    if settings.phase == AUTH_PHASE:
        app.add_middleware(GoogleAuthorization, allowlist=settings.auth_allowlist)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts, www_redirect=False)
    app.add_middleware(ReadOnlyBoundary, stage=settings.environment == "stage")

    @app.exception_handler(DatabaseUnavailable)
    async def unavailable(request, exc):
        LOG.warning("Synthetic database read unavailable: %s", exc.code)
        return JSONResponse(
            {"error": "database_unavailable", "message": "Synthetic database reads are unavailable."},
            status_code=503,
        )

    @app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.api_route("/readyz", methods=["GET", "HEAD"], include_in_schema=False)
    def ready():
        if reader is not None:
            reader.sample()
        return {"status": "ready"}

    @app.api_route(BASE_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    def canonical_path():
        return RedirectResponse(f"{BASE_PATH}/", status_code=308)

    @app.api_route(f"{BASE_PATH}/", methods=["GET", "HEAD"], include_in_schema=False)
    def index():
        return HTMLResponse(page)

    @app.api_route(f"{BASE_PATH}/api/status", methods=["GET", "HEAD"], include_in_schema=False)
    def status():
        return {
            "application": APPLICATION,
            "environment": settings.environment,
            "release": settings.release,
            "phase": settings.phase,
        }

    if settings.phase == AUTH_PHASE:
        @app.api_route(SESSION_PATH, methods=["GET", "HEAD"], include_in_schema=False)
        def session(request: Request):
            identity = request.state.hosting_identity
            return {
                "authenticated": True,
                "authorized": request.state.hosting_authorized,
                "identity": {"provider": identity.provider, "subject": identity.subject},
            }

    if reader is not None:
        @app.api_route(f"{BASE_PATH}/api/sample", methods=["GET", "HEAD"], include_in_schema=False)
        def sample():
            return reader.sample()

    return app
