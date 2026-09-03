#!/usr/bin/env python3
"""Drive a full rehearsal: the real app plays a real (mock) draft.

This is the closest thing to the actual event that can be run beforehand.
A mock draft server impersonates the platform, the application polls it exactly
as it will tomorrow, and this script plays the operator's part by asking the
app for a recommendation and claiming it -- the same two actions a human
performs.

What it proves that other tests do not: the live polling path, the sync logic,
the HTTP client and the decision engine all work together against a board that
is changing underneath them.

    python3 scripts/rehearse.py            # full 150-pick rehearsal
    python3 scripts/rehearse.py --speed 60 # faster
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import config

ROOT = pathlib.Path(__file__).resolve().parent.parent


def get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def post(url, payload, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seat", type=int, default=5)
    ap.add_argument("--speed", type=float, default=40.0)
    ap.add_argument("--mock-port", type=int, default=8899)
    ap.add_argument("--app-port", type=int, default=8778)
    args = ap.parse_args()

    cfg = config.load()
    state_file = ROOT / "data" / "rehearsal-session.json"
    state_file.unlink(missing_ok=True)

    mock_url = f"http://127.0.0.1:{args.mock_port}"
    app_url = f"http://127.0.0.1:{args.app_port}"

    mock = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "mock_draft.py"),
         "--port", str(args.mock_port), "--seat", str(args.seat),
         "--speed", str(args.speed)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
    )
    env = {**dict(__import__("os").environ),
           "FFOPT_API_V1": f"{mock_url}/v1",
           "PYTHONPATH": str(ROOT),
           "FFOPT_SESSION_PATH": str(state_file)}
    app = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "serve.py"),
         "--no-browser", "--port", str(args.app_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT, env=env,
    )

    try:
        for _ in range(60):
            try:
                get(f"{app_url}/api/state", timeout=5)
                get(f"{mock_url}/v1/draft/{cfg.draft_id}/picks", timeout=5)
                break
            except Exception:
                time.sleep(1)
        else:
            print("servers did not start")
            return 1

        post(f"{app_url}/api/seat", {"seat": args.seat})
        post(f"{app_url}/api/mode", {"mode": "live"})
        print(f"rehearsal: seat {args.seat}, {args.speed:.0f}x speed\n")

        my_picks = set(cfg.pick_numbers(args.seat))
        made, latencies, deadline = 0, [], time.time() + 900

        while time.time() < deadline:
            post(f"{app_url}/api/sync", {})
            state = get(f"{app_url}/api/state")
            if state["complete"]:
                break
            if not state["my_turn"]:
                time.sleep(0.3)
                continue

            start = time.perf_counter()
            advice = get(f"{app_url}/api/recommend", timeout=90)
            latency = time.perf_counter() - start
            latencies.append(latency)
            if not advice.get("picks"):
                print("no advice returned")
                return 1
            choice = advice["picks"][0]

            result = post(f"{mock_url}/", {"player_id": choice["player_id"]})
            if not result.get("ok"):
                print(f"mock rejected the claim: {result.get('error')}")
                return 1
            made += 1
            print("  round %2d  took %-24s %-4s  (advice in %.2fs)"
                  % (state["round"], choice["name"], choice["pos"], latency))
            post(f"{app_url}/api/sync", {})

        final = get(f"{app_url}/api/state")
        roster = final["roster"]
        counts = collections.Counter(p["pos"] for p in roster)

        print("\n" + "=" * 62)
        print("REHEARSAL RESULT")
        print("=" * 62)
        print("  picks made by the tool : %d" % made)
        print("  draft complete         : %s" % final["complete"])
        print("  roster size            : %d / %d" % (len(roster), cfg.rounds))
        print("  composition            : %s" % dict(counts))
        print("  unfilled slots         : %s" % (final["unfilled"] or "none"))
        if latencies:
            print("  advice latency         : median %.2fs, worst %.2fs"
                  % (sorted(latencies)[len(latencies) // 2], max(latencies)))

        problems = []
        if len(roster) != cfg.rounds:
            problems.append(f"roster has {len(roster)}, expected {cfg.rounds}")
        if final["unfilled"]:
            problems.append(f"cannot field: {final['unfilled']}")
        if counts.get("QB", 0) > 2:
            problems.append(f"{counts['QB']} quarterbacks")
        if counts.get("K", 0) > 1 or counts.get("DEF", 0) > 1:
            problems.append("surplus kicker or defense")
        if latencies and max(latencies) > cfg.pick_timer_seconds / 2:
            problems.append(f"advice took {max(latencies):.0f}s")

        print()
        if problems:
            print("  FAILED: " + "; ".join(problems))
            return 1
        print("  PASSED: legal roster, no surplus, advice well inside the timer")
        return 0
    finally:
        for proc in (app, mock):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        state_file.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
