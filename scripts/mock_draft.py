#!/usr/bin/env python3
"""A mock draft server that impersonates the platform API.

Purpose: rehearse against a draft that is actually running. The unit tests
exercise the pieces and the dry run exercises the engine, but neither touches
the live polling path -- the HTTP client, the sync logic, the browser refresh
loop -- which is precisely the machinery that has to work tomorrow and has
never been run against a draft in progress.

This serves the same endpoints the real platform does, so the application talks
to it unmodified:

    # terminal 1
    python3 scripts/mock_draft.py --seat 5 --speed 8

    # terminal 2
    FFOPT_API_V1=http://127.0.0.1:8899/v1 python3 scripts/serve.py

Opponents claim on a timer using consensus order with a geometric reach, the
same model the optimizer assumes, so the board evolves plausibly. Your seat is
left for you: the draft pauses when it is your turn and resumes once you claim,
exactly like the real thing.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import availability, config, pool, shrinkage
from ffopt import client as real_client


class MockDraft:
    """A draft that advances on a timer, pausing on the operator's turn."""

    def __init__(self, cfg, board, seat: int, speed: float, seed: int = 0):
        self.cfg = cfg
        self.board = board
        self.by_id = {i.player_id: i for i in board}
        self.seat = seat
        self.speed = max(speed, 0.1)
        self.rng = random.Random(seed)
        self.model = availability.OpponentModel(
            reach=availability.DEFAULT_REACH, rng=self.rng
        )
        self.picks: list[dict] = []
        self.claimed: set[str] = set()
        self.lock = threading.RLock()
        self.started = time.time()
        self.paused_for_me = False

    @property
    def total(self) -> int:
        return self.cfg.rounds * self.cfg.num_agents

    def next_pick_no(self) -> int:
        return len(self.picks) + 1

    def seat_on_clock(self) -> int | None:
        n = self.next_pick_no()
        return None if n > self.total else self.cfg.seat_of_pick(n)

    def _record(self, item: pool.Item, seat: int) -> None:
        n = self.next_pick_no()
        self.picks.append({
            "player_id": item.player_id,
            "picked_by": self.cfg.my_user_id if seat == self.seat else f"mock_user_{seat}",
            "draft_slot": seat,
            "pick_no": n,
            "round": (n - 1) // self.cfg.num_agents + 1,
            "metadata": {
                "first_name": item.name.split(" ")[0],
                "last_name": " ".join(item.name.split(" ")[1:]),
                "position": item.pos,
                "team": item.team,
            },
        })
        self.claimed.add(item.player_id)

    def opponent_pick(self) -> pool.Item | None:
        """Claim for whoever is on the clock, respecting rough slot limits."""
        seat = self.seat_on_clock()
        if seat is None:
            return None
        held: dict[str, int] = {}
        for p in self.picks:
            if p["draft_slot"] == seat:
                item = self.by_id.get(p["player_id"])
                if item:
                    held[item.pos] = held.get(item.pos, 0) + 1
        caps = {"QB": 2, "RB": 6, "WR": 6, "TE": 3, "K": 1, "DEF": 1}
        rounds_left = self.cfg.rounds - len(
            [p for p in self.picks if p["draft_slot"] == seat]
        )
        eligible = []
        for item in self.board:
            if item.player_id in self.claimed:
                continue
            if held.get(item.pos, 0) >= caps.get(item.pos, 6):
                continue
            # Nobody takes a kicker or defense until the end.
            if item.pos in ("K", "DEF") and rounds_left > 2:
                continue
            eligible.append(item)
            if len(eligible) > availability.MAX_REACH + 1:
                break
        if not eligible:
            eligible = [i for i in self.board if i.player_id not in self.claimed][:1]
        if not eligible:
            return None
        idx = self.model.claim(list(range(len(eligible))), deterministic=False)
        chosen = eligible[idx]
        self._record(chosen, seat)
        return chosen

    def claim_for_me(self, player_id: str) -> pool.Item:
        with self.lock:
            if self.seat_on_clock() != self.seat:
                raise ValueError("it is not your turn")
            item = self.by_id.get(player_id)
            if item is None or item.player_id in self.claimed:
                raise ValueError("unknown or already-claimed player")
            self._record(item, self.seat)
            self.paused_for_me = False
            return item

    def run(self, auto_me: bool = False) -> None:
        """Advance the draft on a timer until it completes."""
        interval = self.cfg.pick_timer_seconds / self.speed
        while True:
            with self.lock:
                seat = self.seat_on_clock()
                if seat is None:
                    print("\n[mock] draft complete: %d picks" % len(self.picks))
                    return
                if seat == self.seat and not auto_me:
                    if not self.paused_for_me:
                        self.paused_for_me = True
                        print("\n[mock] pick %d — YOUR TURN (seat %d). "
                              "Waiting for you to claim." % (self.next_pick_no(), seat))
                    time.sleep(0.25)
                    continue
                item = self.opponent_pick()
                if item is None:
                    return
                print("[mock] pick %3d  seat %2d  %-24s %s"
                      % (len(self.picks), seat, item.name, item.pos), flush=True)
            time.sleep(interval)


def make_handler(mock: MockDraft, league_raw: dict, draft_raw: dict):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _json(self, payload, code=200):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = urlparse(self.path).path
            did = mock.cfg.draft_id
            with mock.lock:
                if path == f"/v1/draft/{did}/picks":
                    return self._json(list(mock.picks))
                if path == f"/v1/draft/{did}":
                    d = dict(draft_raw)
                    d["draft_order"] = {mock.cfg.my_user_id: mock.seat}
                    d["status"] = "drafting" if mock.picks else "pre_draft"
                    return self._json(d)
                if path == f"/v1/league/{mock.cfg.league_id}":
                    return self._json(league_raw)
            return self._json({"error": "not mocked", "path": path}, 404)

        def do_POST(self):
            """Claim for the operator, so the mock can be driven from a script."""
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            try:
                item = mock.claim_for_me(str(body.get("player_id")))
                return self._json({"ok": True, "name": item.name})
            except Exception as exc:  # noqa: BLE001
                return self._json({"ok": False, "error": str(exc)}, 400)

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--seat", type=int, default=5)
    ap.add_argument("--speed", type=float, default=8.0,
                    help="how many times faster than the real pick timer")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--auto-me", action="store_true",
                    help="let the mock pick for your seat too (unattended run)")
    args = ap.parse_args()

    cfg = config.load()
    items = pool.build(real_client.projections(cfg.season), cfg.scoring_weights)
    board = availability.consensus_order(
        [i for i in shrinkage.shrink(items, 0.7) if i.adp is not None]
    )
    mock = MockDraft(cfg, board, args.seat, args.speed, args.seed)

    league_raw = real_client.league(cfg.league_id)
    draft_raw = real_client.draft(cfg.draft_id)

    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), make_handler(mock, league_raw, draft_raw)
    )
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("mock draft server on http://127.0.0.1:%d" % args.port)
    print("  your seat : %d" % args.seat)
    print("  speed     : %.1fx (a pick every %.1fs)"
          % (args.speed, cfg.pick_timer_seconds / args.speed))
    print("  board     : %d players" % len(board))
    print()
    print("point the app at it:")
    print("  FFOPT_API_V1=http://127.0.0.1:%d/v1 python3 scripts/serve.py" % args.port)
    print()
    try:
        mock.run(auto_me=args.auto_me)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
