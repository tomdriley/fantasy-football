"""HTTP interface for the draft session.

Pure standard library. No framework is installed and none is added: installing
packages hours before a one-shot, time-critical event is a risk with no
compensating benefit, and the interface is a dozen JSON endpoints over an engine
that already exists.

Two properties matter more than features here:

* **Nothing may hang.** Every handler either returns quickly or returns a
  degraded answer. Recommendations are cached per board state so that polling
  the interface never re-runs a simulation that has not changed.
* **Nothing may crash.** An unhandled exception during a live draft is
  indistinguishable from the tool being gone, so handlers convert failures into
  JSON errors and the advice endpoints fall back to the instant static ordering.
"""

from __future__ import annotations

import json
import pathlib
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import config, session

WEB_ROOT = config.REPO_ROOT / "web"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".ico": "image/x-icon",
}


class DraftService:
    """Thread-safe wrapper around a session, with recommendation caching."""

    def __init__(self, sess: session.DraftSession):
        self.session = sess
        self._lock = threading.RLock()
        self._cache: dict[tuple, list[dict]] = {}

    def _key(self, kind: str) -> tuple:
        s = self.session
        return (kind, s.picks_made, s.seat, len(s.board), hash(frozenset(s.claimed_ids)))

    def invalidate(self) -> None:
        self._cache.clear()

    # -- reads ----------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            return self.session.snapshot()

    def recommend(self, trials: int = 30) -> dict:
        with self._lock:
            key = self._key("rec")
            if key not in self._cache:
                self._cache.clear()
                self._cache[key] = self.session.recommendations(trials=trials)
            return {"picks": self._cache[key], "cached": True}

    def panic(self) -> dict:
        with self._lock:
            return {"picks": self.session.panic()}

    def search(self, query: str) -> dict:
        with self._lock:
            return {"results": self.session.search(query)}

    # -- writes ---------------------------------------------------------
    def _mutate(self, fn: Callable[[], Any]) -> dict:
        with self._lock:
            result = fn()
            self.invalidate()
            payload = {"ok": True, "state": self.session.snapshot()}
            if result is not None:
                payload["item"] = (
                    result if isinstance(result, dict)
                    else {"name": getattr(result, "name", str(result)),
                          "pos": getattr(result, "pos", None)}
                )
            return payload

    def claim(self, player_id: str | None, query: str | None) -> dict:
        with self._lock:
            if player_id is None:
                if not query:
                    raise session.SessionError("provide a player id or a search query")
                results = self.session.search(query)
                if len(results) != 1:
                    if not results:
                        already = self.session.find_claimed(query)
                        if already is not None:
                            return {
                                "ok": False, "results": [],
                                "state": self.session.snapshot(),
                                "error": f"{already.name} was already taken",
                            }
                    return {
                        "ok": False, "ambiguous": bool(results), "results": results,
                        "state": self.session.snapshot(),
                        "error": (f"{len(results)} players match {query!r}"
                                  if results else f"no match for {query!r}"),
                    }
                player_id = results[0]["player_id"]
            return self._mutate(lambda: self.session.claim(player_id))

    def undo(self) -> dict:
        return self._mutate(self.session.undo)

    def correct(self, pick: int, player_id: str) -> dict:
        return self._mutate(lambda: self.session.correct(pick, player_id))

    def insert(self, pick: int, player_id: str) -> dict:
        return self._mutate(lambda: self.session.insert(pick, player_id))

    def remove(self, pick: int) -> dict:
        return self._mutate(lambda: self.session.remove(pick))

    def reset(self) -> dict:
        return self._mutate(self.session.reset)

    def set_mode(self, mode: str) -> dict:
        return self._mutate(lambda: self.session.set_mode(mode))

    def set_seat(self, seat: int | None) -> dict:
        return self._mutate(lambda: self.session.set_seat(seat))

    def sync(self) -> dict:
        with self._lock:
            result = self.session.sync()
            self.invalidate()
            return {**result, "state": self.session.snapshot()}


def make_handler(service: DraftService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ffopt"

        # -- plumbing ---------------------------------------------------
        def log_message(self, fmt, *args):  # noqa: A003 - quieten the console
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, payload: Any, code: int = 200) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def _static(self, name: str) -> None:
            path = (WEB_ROOT / name).resolve()
            if not str(path).startswith(str(WEB_ROOT.resolve())) or not path.is_file():
                self._json({"error": "not found"}, 404)
                return
            self._send(
                200, path.read_bytes(),
                CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
            )

        # -- routes -----------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            url = urlparse(self.path)
            route = url.path
            try:
                if route in ("/", "/index.html"):
                    return self._static("index.html")
                if route.startswith("/api/"):
                    query = parse_qs(url.query)
                    if route == "/api/state":
                        return self._json(service.state())
                    if route == "/api/recommend":
                        trials = int((query.get("trials") or [30])[0])
                        return self._json(service.recommend(max(1, min(trials, 200))))
                    if route == "/api/panic":
                        return self._json(service.panic())
                    if route == "/api/search":
                        return self._json(service.search((query.get("q") or [""])[0]))
                    return self._json({"error": "unknown endpoint"}, 404)
                return self._static(route.lstrip("/"))
            except session.SessionError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001 - never die mid-draft
                self._json({"ok": False, "error": str(exc),
                            "trace": traceback.format_exc()[-400:]}, 500)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            body = self._body()
            try:
                if route == "/api/claim":
                    return self._json(service.claim(body.get("player_id"), body.get("query")))
                if route == "/api/undo":
                    return self._json(service.undo())
                if route == "/api/correct":
                    return self._json(service.correct(int(body["pick"]), body["player_id"]))
                if route == "/api/insert":
                    return self._json(service.insert(int(body["pick"]), body["player_id"]))
                if route == "/api/remove":
                    return self._json(service.remove(int(body["pick"])))
                if route == "/api/reset":
                    return self._json(service.reset())
                if route == "/api/mode":
                    return self._json(service.set_mode(body.get("mode", "")))
                if route == "/api/seat":
                    seat = body.get("seat")
                    return self._json(service.set_seat(int(seat) if seat else None))
                if route == "/api/sync":
                    return self._json(service.sync())
                return self._json({"error": "unknown endpoint"}, 404)
            except session.SessionError as exc:
                self._json({"ok": False, "error": str(exc),
                            "state": service.state()}, 400)
            except (KeyError, ValueError, TypeError) as exc:
                self._json({"ok": False, "error": f"bad request: {exc}",
                            "state": service.state()}, 400)
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc),
                            "trace": traceback.format_exc()[-400:]}, 500)

    return Handler


def build_service(load_board: bool = True) -> DraftService:
    sess = session.DraftSession()
    sess.load()
    if load_board:
        sess.load_board()
    return DraftService(sess)


def serve(port: int = 8777, service: DraftService | None = None) -> ThreadingHTTPServer:
    service = service or build_service()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(service))
    server.daemon_threads = True
    return server
