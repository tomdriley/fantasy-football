"""Strict same-origin JSON request validation for the sole synthetic mutation."""

import asyncio
import json
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .auth import Identity, _unique_object

WRITE_PATH = "/fantasy-football/api/test-write"
WRITE_PAGE = "/fantasy-football/test-write"
SCRIPT_PATH = "/fantasy-football/static/test-write.js"
MAX_BODY_BYTES = 128
BODY_TIMEOUT_SECONDS = 5
ACTION = {"action": "record"}


def request_headers_valid(request, origin):
    headers = {}
    checked = {b"origin", b"content-type", b"x-ffopt-write", b"sec-fetch-site", b"content-length", b"content-encoding"}
    for name, value in request.scope.get("headers", []):
        name = name.lower()
        if name in checked:
            if name in headers:
                return False
            headers[name] = value
    if (
        headers.get(b"origin") != origin.encode("ascii")
        or headers.get(b"x-ffopt-write") != b"record-v1"
        or headers.get(b"content-type") != b"application/json"
        or headers.get(b"sec-fetch-site", b"same-origin") != b"same-origin"
        or b"content-encoding" in headers
    ):
        return False
    if b"content-length" in headers:
        length = headers[b"content-length"]
        if not length.isdigit() or len(length) > 3 or int(length) > MAX_BODY_BYTES:
            return False
    return True


def install_write_routes(app, store, origin, allowlist):
    directory = Path(__file__).parent / "static"
    page = (directory / "test-write.html").read_text(encoding="utf-8")
    script = (directory / "test-write.js").read_text(encoding="utf-8")

    def authorized_identity(request):
        identity = getattr(request.state, "hosting_identity", None)
        return identity if isinstance(identity, Identity) and identity in allowlist else None

    @app.api_route(WRITE_PAGE, methods=["GET", "HEAD"], include_in_schema=False)
    def write_page():
        return HTMLResponse(page)

    @app.api_route(SCRIPT_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    def write_script():
        return Response(script, media_type="text/javascript")

    @app.api_route(WRITE_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    def marker_status(request: Request):
        identity = authorized_identity(request)
        if identity is None:
            return JSONResponse({"error": "not_authorized"}, status_code=403)
        return store.status(identity)

    @app.post(WRITE_PATH, include_in_schema=False)
    async def record_marker(request: Request):
        identity = authorized_identity(request)
        if identity is None:
            return JSONResponse({"error": "not_authorized"}, status_code=403)
        if not request_headers_valid(request, origin):
            return JSONResponse({"error": "invalid_write_request"}, status_code=403)
        if request.scope.get("query_string"):
            return JSONResponse({"error": "invalid_write_action"}, status_code=400)
        body = bytearray()
        try:
            async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        return JSONResponse({"error": "request_too_large"}, status_code=413)
                    body.extend(chunk)
        except TimeoutError:
            return JSONResponse({"error": "request_timeout"}, status_code=408)
        except ClientDisconnect:
            return JSONResponse({"error": "request_incomplete"}, status_code=400)
        length = request.headers.get("content-length")
        if length is not None and int(length) != len(body):
            return JSONResponse({"error": "request_incomplete"}, status_code=400)
        try:
            payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            return JSONResponse({"error": "invalid_write_action"}, status_code=400)
        if type(payload) is not dict or payload != ACTION:
            return JSONResponse({"error": "invalid_write_action"}, status_code=400)
        return await run_in_threadpool(store.record, identity)
