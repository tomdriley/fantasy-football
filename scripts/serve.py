#!/usr/bin/env python3
"""Start the draft web interface.

    python3 scripts/serve.py               # http://127.0.0.1:8777
    python3 scripts/serve.py --port 9000
    python3 scripts/serve.py --offline     # never touch the network
    python3 scripts/serve.py --fresh       # discard any saved board

Binds to localhost only. The draft board is persisted server-side, so closing
or refreshing the browser does not lose anything.
"""
import argparse
import pathlib
import sys
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import webapp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--offline", action="store_true",
                    help="start in manual mode and never poll the API")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="discard any saved board and start empty")
    args = ap.parse_args()

    # Unbuffered so the startup banner appears even when piped to a log.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    service = webapp.build_service(fresh=args.fresh)
    sess = service.session
    if args.offline:
        sess.set_mode("manual")

    url = f"http://127.0.0.1:{args.port}/"
    server = webapp.serve(args.port, service)
    print(f"board  : {len(sess.board)} players")
    print(f"mode   : {sess.mode}")
    if getattr(sess, "restored", False):
        print("")
        print(f"  !! RESUMED: {sess.picks_made} pick(s) restored from an earlier session.")
        print("     If the draft has not started yet this is leftover state:")
        print("     stop, and rerun with  python3 scripts/serve.py --fresh")
        print("")
    else:
        print(f"picks  : {sess.picks_made} (starting clean)")
        if sess.stale_state_reason:
            print(f"         ignored a saved board: {sess.stale_state_reason}")
    print(f"open   : {url}")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
