#!/usr/bin/env python3
"""Start the draft web interface.

    python3 scripts/serve.py               # http://127.0.0.1:8777
    python3 scripts/serve.py --port 9000
    python3 scripts/serve.py --offline     # never touch the network

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
    args = ap.parse_args()

    service = webapp.build_service()
    if args.offline:
        service.session.set_mode("manual")

    url = f"http://127.0.0.1:{args.port}/"
    server = webapp.serve(args.port, service)
    print(f"board: {len(service.session.board)} players")
    print(f"mode : {service.session.mode}")
    print(f"open : {url}")
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
