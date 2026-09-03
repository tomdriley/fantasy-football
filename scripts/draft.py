#!/usr/bin/env python3
"""Live draft advisor. Polls the claim feed and prints recommendations.

Usage:
    python3 scripts/draft.py            # auto-detect seat, poll continuously
    python3 scripts/draft.py --seat 5   # force a seat before order is assigned
    python3 scripts/draft.py --once     # single snapshot
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import client, config, live


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seat", type=int, default=None)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--trials", type=int, default=30)
    args = ap.parse_args()

    cfg = config.load()
    t0 = time.time()
    board = live.load_board(cfg)
    print(f"board loaded: {len(board)} players in {time.time()-t0:.1f}s", file=sys.stderr)

    last = -1
    while True:
        state = live.read_state(cfg, board, cfg.my_user_id)
        seat = args.seat or state.my_seat
        if seat is None:
            draft = client.draft(cfg.draft_id)
            order = draft.get("draft_order") or {}
            seat = order.get(cfg.my_user_id)
        if seat is None:
            print("seat not yet assigned; pass --seat to plan ahead", file=sys.stderr)
            if args.once:
                return 1
            time.sleep(args.interval)
            continue

        if state.picks_made != last or args.once:
            t = time.time()
            recs, vor = live.recommend_now(cfg, board, state, seat, trials=args.trials)
            print(live.render(cfg, board, state, seat, recs, vor))
            print(f"  computed in {time.time()-t:.2f}s", file=sys.stderr)
            last = state.picks_made
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
