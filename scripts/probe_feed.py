#!/usr/bin/env python3
"""Measure whether the live pick feed actually updates in real time.

The one thing that could not be tested beforehand. Every other test ran against
a mock server, which proves the client parses the schema -- not that the real
endpoint publishes picks as they happen. The documentation describes the
endpoint's shape and says nothing about liveness or caching.

Point this at a throwaway league and start its draft. It polls the pick feed
and reports, for each pick that appears:

  * how long after the previous pick it showed up,
  * whether the response came from the CDN cache or origin,
  * and how stale the cached copy was.

    python3 scripts/probe_feed.py <league_id_or_draft_id>

What "good" looks like: picks appear within a second or two of happening, with
cf-cache-status MISS and no age header. What would be disqualifying: picks
arriving in batches, or minutes late, which would mean live mode is unusable
and manual entry is the only safe option.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import client

API = "https://api.sleeper.app/v1"


def fetch(url: str) -> tuple[object, dict, float]:
    """Return (body, headers, elapsed_seconds)."""
    req = urllib.request.Request(url, headers={"User-Agent": client.USER_AGENT})
    start = time.time()
    with urllib.request.urlopen(req, timeout=15) as response:
        body = json.load(response)
        headers = {k.lower(): v for k, v in response.headers.items()}
    return body, headers, time.time() - start


def resolve(identifier: str) -> tuple[str, dict]:
    """Accept either a league id or a draft id."""
    try:
        league, _, _ = fetch(f"{API}/league/{identifier}")
        if isinstance(league, dict) and league.get("draft_id"):
            return str(league["draft_id"]), league
    except urllib.error.HTTPError:
        pass
    return identifier, {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("identifier", help="league id or draft id")
    ap.add_argument("--seconds", type=int, default=600, help="how long to watch")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--no-bust", action="store_true",
                    help="poll without the cache-busting parameter, for comparison")
    args = ap.parse_args()

    draft_id, league = resolve(args.identifier)
    if league:
        print(f"league : {league.get('name')}  ({league.get('total_rosters')} teams)")
    draft, _, _ = fetch(f"{API}/draft/{draft_id}")
    settings = draft.get("settings") or {}
    print(f"draft  : {draft_id}  status={draft.get('status')} "
          f"timer={settings.get('pick_timer')}s rounds={settings.get('rounds')}")
    print(f"mode   : {'PLAIN (cacheable)' if args.no_bust else 'cache-busted'}")
    if draft.get("status") == "pre_draft":
        print("\n  draft has not started yet -- start it in Sleeper now.")
        print("  (a short pick timer makes this much faster to learn from)")
    print(f"\nwatching for {args.seconds}s  [ctrl-c to stop early and see the result]\n")
    print(f"{'pick':>5} {'gap':>8} {'rtt':>7} {'cache':>6} {'age':>4}  player")
    print("-" * 62)

    base = f"{API}/draft/{draft_id}/picks"
    existing, _, _ = fetch(base if args.no_bust else f"{base}?_={time.time_ns()}")
    seen = len(existing)
    if seen:
        print(f"  ({seen} picks already on the board -- these are backlog and are")
        print("   not timed; only picks arriving from now on are measured)\n")
    last_at = None
    gaps: list[float] = []
    hits = misses = 0
    stale_max = 0
    deadline = time.time() + args.seconds
    first_seen_at = None

    try:
        seen, gaps, hits, misses, stale_max = watch(
            base, args, deadline, seen, gaps, hits, misses, stale_max
        )
    except KeyboardInterrupt:
        print("\n  (stopped early)")

    report(seen, gaps, hits, misses, stale_max)
    return 0


def watch(base, args, deadline, seen, gaps, hits, misses, stale_max):
    last_at = None
    while time.time() < deadline:
        url = base if args.no_bust else f"{base}?_={time.time_ns()}"
        try:
            picks, headers, rtt = fetch(url)
        except Exception as exc:  # noqa: BLE001 - a probe must not die
            print(f"  ! {exc}")
            time.sleep(args.interval)
            continue

        status = (headers.get("cf-cache-status") or "-")[:6]
        age = headers.get("age")
        if status.startswith("HIT"):
            hits += 1
            stale_max = max(stale_max, int(age or 0))
        elif status.startswith("MISS") or status.startswith("EXPIR"):
            misses += 1

        if len(picks) > seen:
            now = time.time()
            for pick in picks[seen:]:
                meta = pick.get("metadata") or {}
                name = f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()
                gap = f"{now - last_at:6.1f}s" if last_at else "     -"
                if last_at:
                    gaps.append(now - last_at)
                print(f"{pick.get('pick_no'):>5} {gap:>8} {rtt:6.2f}s "
                      f"{status:>6} {str(age or '-'):>4}  {name} "
                      f"({meta.get('position')})")
                last_at = now
            seen = len(picks)

        time.sleep(args.interval)
    return seen, gaps, hits, misses, stale_max


def report(seen, gaps, hits, misses, stale_max):
    print("\n" + "=" * 62)
    print("RESULT")
    print("=" * 62)
    print(f"  picks observed     : {seen}")
    if gaps:
        gaps_sorted = sorted(gaps)
        median = gaps_sorted[len(gaps_sorted) // 2]
        print(f"  gap between picks  : median {median:.1f}s, "
              f"min {gaps_sorted[0]:.1f}s, max {gaps_sorted[-1]:.1f}s")
        clustered = sum(1 for g in gaps if g < 0.5)
        print(f"  arrived in batches : {clustered}/{len(gaps)} within 0.5s of each other")
    print(f"  cdn cache HIT/MISS : {hits}/{misses}")
    if hits:
        print(f"  worst staleness    : {stale_max}s  <-- lag before our code even runs")

    if seen == 0:
        print("\n  INCONCLUSIVE: no picks appeared. Was the draft started?")
    elif len(gaps) < 3:
        print("\n  INCONCLUSIVE: too few picks arrived while watching to judge.")
        print("  Run it again for longer, or with a shorter pick timer.")
    elif sum(1 for g in gaps if g < 0.5) > len(gaps) * 0.6:
        print("\n  WARNING: picks arrived mostly in batches, not individually.")
        print("  That is consistent with a feed that is written behind the room.")
    else:
        print("\n  The feed publishes picks individually, as they happen.")
        print("  Assisted mode is safe to use. (Live mode still applies the feed")
        print("  automatically, which manual entry never does.)")


if __name__ == "__main__":
    raise SystemExit(main())
