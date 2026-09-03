#!/usr/bin/env python3
"""Live draft advisor, with manual fallback if the pick feed is unavailable.

Modes:
    python3 scripts/draft.py                 poll the API, fall back to manual on failure
    python3 scripts/draft.py --manual        manual entry from the start
    python3 scripts/draft.py --seat 5        force a seat before the order is assigned
    python3 scripts/draft.py --once          one snapshot, no loop

Manual commands (type a surname; it will fuzzy match):
    gibbs          an opponent claimed Gibbs
    me gibbs       *you* claimed Gibbs
    undo           take back the last entry
    board          show recent claims
    sync           try the API again and resync
    reset          clear the manual board
    quit
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import client, config, live, manual


def advise(cfg, board, state, seat, trials):
    t = time.time()
    recs, vor = live.recommend_now(cfg, board, state, seat, trials=trials)
    print(live.render(cfg, board, state, seat, recs, vor))
    print(f"  ({time.time()-t:.1f}s)", file=sys.stderr)


def resolve_seat(cfg, args, state_seat):
    if args.seat:
        return args.seat
    if state_seat:
        return state_seat
    try:
        order = (client.draft(cfg.draft_id) or {}).get("draft_order") or {}
        return order.get(cfg.my_user_id)
    except Exception:
        return None


def manual_loop(cfg, board, seat, trials):
    mb = manual.ManualBoard(cfg)
    if mb.load():
        print(f"restored {mb.picks_made} claims from a previous session "
              f"(use 'reset' to clear)")
    else:
        try:
            n = manual.sync_from_feed(mb, client.draft_picks(cfg.draft_id), cfg.my_user_id)
            if n:
                print(f"seeded {n} claims from the API before falling back")
        except Exception:
            pass

    by_id = {i.player_id: i for i in board}
    pending: list = []
    pending_mine = False
    print("\nMANUAL MODE. Type a surname as each pick happens. 'me <name>' for your own.")
    print("If several players match, pick one by number. Commands: undo | board | sync | reset | quit\n")

    while True:
        state = live.BoardState(
            claimed=mb.claimed, my_roster=mb.my_roster(board),
            picks_made=mb.picks_made, my_seat=seat,
            on_the_clock=mb.seat_on_clock(),
        )
        if mb.picks_made < cfg.rounds * cfg.num_agents:
            advise(cfg, board, state, seat, trials)

        try:
            raw = input(f"[pick {mb.picks_made + 1}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw:
            continue
        cmd = raw.lower()

        # Numeric reply resolves a previous ambiguous lookup.
        if pending and cmd.isdigit():
            choice = int(cmd)
            if 1 <= choice <= len(pending):
                chosen = pending[choice - 1]
                mb.claim(chosen, pending_mine)
                print(f"  recorded{' (YOURS)' if pending_mine else ''}: "
                      f"{chosen.name} ({chosen.pos})")
                pending = []
                continue
            print(f"  pick 1-{len(pending)}")
            continue
        pending = []

        if cmd in ("quit", "q", "exit"):
            return 0
        if cmd in ("undo", "u"):
            pid = mb.undo()
            item = by_id.get(pid) if pid else None
            print(f"  undone: {item.name if item else '(nothing to undo)'}")
            continue
        if cmd in ("board", "b"):
            recent = mb.claims[-12:]
            for n, (pid, mine) in enumerate(recent, mb.picks_made - len(recent) + 1):
                it = by_id.get(pid)
                print(f"  {n:>3}. {'YOU  ' if mine else '     '}"
                      f"{it.name if it else pid} ({it.pos if it else '?'})")
            continue
        if cmd == "reset":
            mb.reset()
            print("  board cleared")
            continue
        if cmd == "sync":
            try:
                n = manual.sync_from_feed(mb, client.draft_picks(cfg.draft_id), cfg.my_user_id)
                print(f"  resynced {n} claims from the API")
            except Exception as e:
                print(f"  API still unavailable: {e}")
            continue

        mine = False
        query = raw
        for prefix in ("me ", "my ", "+"):
            if cmd.startswith(prefix):
                mine, query = True, raw[len(prefix):].strip()
                break

        available = [i for i in board if i.player_id not in mb.claimed]
        m = manual.find(query, available)
        if m.resolved:
            mb.claim(m.exact, mine)
            print(f"  recorded{' (YOURS)' if mine else ''}: "
                  f"{m.exact.name} ({m.exact.pos})")
        elif m.ambiguous:
            pending, pending_mine = list(m.candidates), mine
            print(f"  {len(m.candidates)} matches - reply with a number:")
            for n, c in enumerate(m.candidates, 1):
                adp = f", adp {c.adp:.0f}" if c.adp else ""
                print(f"     {n}. {c.name} ({c.pos}{adp})")
        else:
            print(f"  no match for {query!r} - check spelling, or try a surname")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seat", type=int, default=None)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--manual", action="store_true", help="skip the API entirely")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--trials", type=int, default=30)
    args = ap.parse_args()

    cfg = config.load()
    t0 = time.time()
    board = live.load_board(cfg)
    print(f"board loaded: {len(board)} players in {time.time()-t0:.1f}s", file=sys.stderr)

    seat = args.seat
    if args.manual:
        if seat is None:
            seat = resolve_seat(cfg, args, None)
        if seat is None:
            print("seat unknown; pass --seat to use manual mode", file=sys.stderr)
            return 1
        return manual_loop(cfg, board, seat, args.trials)

    last = -1
    failures = 0
    while True:
        try:
            state = live.read_state(cfg, board, cfg.my_user_id)
            failures = 0
        except Exception as e:
            failures += 1
            print(f"pick feed unavailable ({e}) [{failures}]", file=sys.stderr)
            if failures >= 3:
                print("\nfalling back to MANUAL entry.\n", file=sys.stderr)
                s = resolve_seat(cfg, args, None)
                if s is None:
                    print("seat unknown; pass --seat", file=sys.stderr)
                    return 1
                return manual_loop(cfg, board, s, args.trials)
            time.sleep(args.interval)
            continue

        seat = resolve_seat(cfg, args, state.my_seat)
        if seat is None:
            print("seat not yet assigned; pass --seat to plan ahead", file=sys.stderr)
            if args.once:
                return 1
            time.sleep(args.interval)
            continue

        if state.picks_made != last or args.once:
            advise(cfg, board, state, seat, args.trials)
            last = state.picks_made
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
