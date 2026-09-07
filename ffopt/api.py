"""Versioned FastAPI interface; domain code remains independent of HTTP."""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import pathlib
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import UUID
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import archive, config
from .jobs import IdempotencyConflict, QueueFull
from .service import AdvisorService

LOG = logging.getLogger(__name__)
WEB_ROOT = config.REPO_ROOT / "web" / "season" / "dist"
AUTH = HTTPBearer(auto_error=False)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CollectionRequest(StrictBody):
    refresh: bool = False
    minimum_pickup_gain: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class EvaluationRequest(StrictBody):
    minimum_pickup_gain: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Job(BaseModel):
    id: str
    kind: Literal["collect", "evaluate"]
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at_ms: int
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    snapshot_id: str | None = None
    result: dict[str, str] | None = None
    error: str | None = None


class JobResponse(BaseModel):
    job: Job


class JobAccepted(JobResponse):
    poll_url: str


class JobList(BaseModel):
    items: list[Job]


class Storage(BaseModel):
    unique_payloads: int
    raw_bytes: int
    compressed_bytes: int


class SnapshotSummary(BaseModel):
    id: str
    league_id: str
    started_at_ms: int
    finished_at_ms: int | None
    week: int | None
    season: str | None
    status: Literal["complete", "incomplete", "invalid", "unfinished"]


class SnapshotList(BaseModel):
    items: list[SnapshotSummary]
    storage: Storage


class Evaluation(BaseModel):
    id: str
    capture_id: str
    recorded_at_ms: int
    engine_fingerprint: str
    report: dict[str, Any]


class EvaluationList(BaseModel):
    items: list[Evaluation]


class SnapshotDetail(BaseModel):
    snapshot: dict[str, Any]
    league: dict[str, str]
    latest_evaluation: Evaluation | None


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class AdviceFreshness(BaseModel):
    state: Literal["recent", "stale", "locks_changed", "unavailable", "historical", "rules_changed"]
    usable: bool
    age_ms: int | None
    message: str
    reasons: list[str]
    threshold_seconds: int
    valid_until_ms: int | None


class AdviceSummary(BaseModel):
    headline: str
    lineup_change_count: int
    injury_count: int
    missing_slots: list[str]
    projected_total: float | None


class AdviceResponse(BaseModel):
    mode: Literal["current", "historical"]
    now_ms: int
    league: dict[str, str]
    snapshot: dict[str, Any] | None
    latest_attempt: dict[str, Any] | None
    freshness: AdviceFreshness
    next_deadline: dict[str, Any] | None
    summary: AdviceSummary
    actions: list[dict[str, Any]]
    lineup: list[dict[str, Any]]
    injuries: list[dict[str, Any]]
    pickups: list[dict[str, Any]]
    warnings: list[str]
    sleeper_url: str
    evaluation_id: str | None
    limitations: list[str]


def content_security_policy(style_nonce: str | None = None) -> str:
    style = "style-src 'self'" + (f" 'nonce-{style_nonce}'" if style_nonce else "")
    return (
        f"default-src 'self'; script-src 'self'; {style}; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )


def error_response(code: str, message: str, status: int, *, headers=None):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def hostname(value: str) -> str:
    """Parse bracketed IPv6 and optional ports without accepting userinfo/paths."""
    parsed = urlsplit("//" + value)
    host = parsed.hostname
    parsed.port  # Validate a supplied port; urlsplit raises for invalid values.
    if (
        not host or parsed.username is not None or parsed.password is not None
        or parsed.path or parsed.query or parsed.fragment or value != value.strip()
    ):
        raise ValueError("invalid host")
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        host = host.encode("idna").decode().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            raise ValueError("invalid hostname")
        return host


class AllowedHosts:
    def __init__(self, app, allowed_hosts):
        self.app = app
        self.allowed = {hostname(h) for h in allowed_hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        values = Headers(scope=scope).getlist("host")
        try:
            valid = len(values) == 1 and hostname(values[0]) in self.allowed
        except ValueError:
            valid = False
        if not valid:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await error_response("invalid_host", "Host is not allowed.", 400)(scope, receive, send)
        return await self.app(scope, receive, send)


class RequestGuard:
    """Bound mutation bodies, require JSON, and reject cross-origin writes."""

    def __init__(self, app, allowed_origins=(), max_body_bytes=65536):
        self.app, self.allowed_origins, self.max_body_bytes = app, set(allowed_origins), max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]}
                if not any(k.lower() == b"content-security-policy" for k, _ in message["headers"]):
                    message["headers"].append((b"content-security-policy", content_security_policy().encode()))
                if scope["path"].startswith("/api/"):
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        if scope["method"] in ("POST", "PUT", "PATCH", "DELETE"):
            origin = headers.get("origin")
            same_origin = f"{scope['scheme']}://{headers.get('host', '')}"
            if origin is not None and origin != same_origin and origin not in self.allowed_origins:
                return await error_response("forbidden_origin", "Origin is not allowed.", 403)(scope, receive, secure_send)
            if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return await error_response("json_required", "Use application/json.", 415)(scope, receive, secure_send)
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > self.max_body_bytes:
                    return await error_response("body_too_large", "Request body exceeds 64 KiB.", 413)(
                        scope, receive, secure_send,
                    )
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            consumed = False

            async def replay_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()

            return await self.app(scope, replay_receive, secure_send)
        return await self.app(scope, receive, secure_send)


def create_app(
    *,
    archive_path: pathlib.Path = archive.DEFAULT_PATH,
    jobs_path: pathlib.Path | None = None,
    rules_path: pathlib.Path = config.RULES_PATH,
    api_token: str | None = None,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]", "testserver"),
    allowed_origins: tuple[str, ...] = (),
    start_worker: bool = True,
    queue_capacity: int = 10,
    frontend_path: pathlib.Path | None = None,
) -> FastAPI:
    if any("*" in value for value in (*allowed_hosts, *allowed_origins)):
        raise ValueError("configure explicit allowed hosts and origins, not wildcards")
    token = api_token if api_token is not None else os.environ.get("FFOPT_API_TOKEN")
    if token is not None and not token.strip():
        raise ValueError("API token must be nonempty when configured")
    jobs_path = pathlib.Path(jobs_path) if jobs_path else pathlib.Path(archive_path).with_name("service.sqlite3")
    service = AdvisorService(pathlib.Path(archive_path), jobs_path, pathlib.Path(rules_path),
                             queue_capacity=queue_capacity)
    frontend_path = pathlib.Path(frontend_path) if frontend_path is not None else WEB_ROOT

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            service.runner.start()
        try:
            yield
        finally:
            if start_worker:
                service.runner.stop()

    app = FastAPI(
        title="Fantasy Football Research API", version="1.0.0",
        description="Read-only Sleeper advice, immutable evidence and explicit shadow comparisons.",
        lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.state.service = service
    app.add_middleware(RequestGuard, allowed_origins=allowed_origins)
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(allowed_origins), allow_credentials=False,
            allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        )
    app.add_middleware(AllowedHosts, allowed_hosts=allowed_hosts)

    async def authorize(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(AUTH)],
    ):
        if token is not None:
            supplied = credentials.credentials if credentials else ""
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                raise HTTPException(401, "API token required.", headers={"WWW-Authenticate": "Bearer"})
        else:
            try:
                loopback = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise HTTPException(403, "Token-free demo mode accepts loopback clients only.")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 429: "busy"}
        return error_response(codes.get(exc.status_code, "request_failed"), str(exc.detail),
                              exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        messages = [".".join(str(x) for x in e["loc"]) + ": " + e["msg"] for e in exc.errors()]
        return error_response("invalid_request", "; ".join(messages), 422)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        LOG.error("Storage operation failed", exc_info=(type(exc), exc, exc.__traceback__))
        return error_response("storage_unavailable", "Storage unavailable; check the server log.", 503)

    @app.exception_handler(archive.ArchiveError)
    @app.exception_handler(OSError)
    async def evidence_error(request, exc):
        LOG.error("Evidence operation failed", exc_info=(type(exc), exc, exc.__traceback__))
        return error_response("evidence_unavailable", "Evidence unavailable; check the server log.", 503)

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"status": "ok", "api_version": "1"}

    @app.get("/readyz", include_in_schema=False)
    def ready():
        service.jobs.stats()
        service.archive.stats()
        if not service.runner.running:
            return error_response("worker_unavailable", "Background worker is not running.", 503)
        return {"status": "ready"}

    router = APIRouter(
        prefix="/api/v1", dependencies=[Depends(authorize)],
        responses={
            status: {"model": ErrorEnvelope}
            for status in (400, 401, 403, 404, 409, 413, 415, 422, 429, 503)
        },
    )

    @router.get("/openapi.json", include_in_schema=False)
    def schema():
        return app.openapi()

    @router.get("/status")
    def status():
        cfg = service.config()
        return {
            "api_version": "1", "mode": "token-protected" if token else "local-demo",
            "league": {
                "name": cfg.raw["league"]["name"], "season": cfg.season,
                "team_name": cfg.raw["my_team"]["team_name"],
            },
            "storage": service.archive.stats(), "jobs": service.jobs.stats(),
            "worker": {"running": service.runner.running}, "server_time_ms": archive.now_ms(),
        }

    @router.get("/snapshots", response_model=SnapshotList)
    def snapshot_list(limit: Annotated[int, Query(ge=1, le=100)] = 20):
        return {"items": service.archive.list(limit), "storage": service.archive.stats()}

    @router.get("/advice", response_model=AdviceResponse)
    def current_advice():
        return service.advice()

    def require_snapshot(snapshot_id: str):
        try:
            return service.archive.manifest(snapshot_id)
        except archive.SnapshotNotFound as exc:
            raise HTTPException(404, "Snapshot not found.") from exc

    @router.get("/advice/{snapshot_id}", response_model=AdviceResponse)
    def historical_advice(snapshot_id: UUID):
        require_snapshot(str(snapshot_id))
        return service.advice(str(snapshot_id))

    @router.get("/snapshots/{snapshot_id}", response_model=SnapshotDetail)
    def snapshot_detail(snapshot_id: UUID):
        require_snapshot(str(snapshot_id))
        return service.snapshot(str(snapshot_id))

    @router.get("/snapshots/{snapshot_id}/evaluations", response_model=EvaluationList)
    def evaluation_history(snapshot_id: UUID, limit: Annotated[int, Query(ge=1, le=100)] = 20):
        require_snapshot(str(snapshot_id))
        return {"items": service.archive.evaluations(str(snapshot_id), limit=limit)[::-1]}

    def enqueue(kind: str, payload: dict, key: str | None):
        try:
            job = service.jobs.enqueue(kind, payload, key)
        except QueueFull as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "5"}) from exc
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        service.runner.wake.set()
        return {"job": job, "poll_url": f"/api/v1/jobs/{job['id']}"}

    @router.post("/collections", response_model=JobAccepted, status_code=202)
    def collect_request(
        body: CollectionRequest,
        idempotency_key: Annotated[str | None, Header(max_length=128, min_length=1)] = None,
    ):
        return enqueue("collect", body.model_dump(), idempotency_key)

    @router.post("/snapshots/{snapshot_id}/evaluations", response_model=JobAccepted, status_code=202)
    def evaluation_request(
        snapshot_id: UUID, body: EvaluationRequest,
        idempotency_key: Annotated[str | None, Header(max_length=128, min_length=1)] = None,
    ):
        if require_snapshot(str(snapshot_id))["status"] != "complete":
            raise HTTPException(409, "Only complete snapshots can be evaluated.")
        return enqueue("evaluate", {**body.model_dump(), "snapshot_id": str(snapshot_id)}, idempotency_key)

    @router.get("/jobs", response_model=JobList)
    def job_list(limit: Annotated[int, Query(ge=1, le=100)] = 20):
        return {"items": service.jobs.list(limit)}

    @router.get("/jobs/{job_id}", response_model=JobResponse)
    def job_detail(job_id: UUID):
        job = service.jobs.get(str(job_id))
        if job is None:
            raise HTTPException(404, "Job not found.")
        return {"job": job}

    app.include_router(router)
    app.mount("/assets", StaticFiles(directory=frontend_path / "assets", check_dir=False), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        index_path = frontend_path / "index.html"
        if not index_path.is_file():
            return error_response(
                "frontend_not_built", "Build the frontend: cd web/season && npm ci && npm run build.", 503,
            )
        nonce = secrets.token_urlsafe(24)
        page = index_path.read_text(encoding="utf-8").replace("__FFOPT_CSP_NONCE__", nonce)
        return HTMLResponse(page, headers={
            "Cache-Control": "no-store", "Content-Security-Policy": content_security_policy(nonce),
        })

    return app
