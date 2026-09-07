#!/usr/bin/env python3
"""Run the in-season REST API and dashboard; localhost demo by default."""

import argparse
import ipaddress
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import uvicorn

from ffopt import archive, config
from ffopt.api import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--archive", type=pathlib.Path, default=archive.DEFAULT_PATH)
    parser.add_argument("--jobs", type=pathlib.Path)
    parser.add_argument("--rules", type=pathlib.Path, default=config.RULES_PATH)
    parser.add_argument("--frontend", type=pathlib.Path, help="optional built frontend directory")
    parser.add_argument("--allowed-host", action="append", default=[])
    parser.add_argument("--allowed-origin", action="append", default=[])
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be in 1..65535")
    try:
        local = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        local = args.host == "localhost"
    if not local and not os.environ.get("FFOPT_API_TOKEN"):
        parser.error("set FFOPT_API_TOKEN before listening beyond loopback")
    if not local and not args.allowed_host:
        parser.error("provide explicit --allowed-host values for remote access")
    hosts = tuple(args.allowed_host or ["127.0.0.1", "localhost", "[::1]"])
    app = create_app(
        archive_path=args.archive, jobs_path=args.jobs, rules_path=args.rules,
        allowed_hosts=hosts, allowed_origins=tuple(args.allowed_origin),
        frontend_path=args.frontend,
    )
    uvicorn.run(
        app, host=args.host, port=args.port, proxy_headers=False,
        limit_concurrency=50, timeout_keep_alive=5, timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
