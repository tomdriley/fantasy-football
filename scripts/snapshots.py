#!/usr/bin/env python3
"""Capture raw decision evidence, replay offline, and compare shadow policies."""

import argparse
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import archive, collector, config, policies
from ffopt.evaluation import evaluate_snapshot as evaluation


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=pathlib.Path, default=archive.DEFAULT_PATH)
    sub = ap.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="one collection run; suitable for an external scheduler")
    collect.add_argument("--week", type=int, choices=range(1, 19))
    collect.add_argument("--refresh", action="store_true", help="bypass archived reuse, including players")
    collect.add_argument("--min-pickup-gain", type=float, help="also evaluate an uncalibrated shadow threshold")
    replay = sub.add_parser("replay", help="offline replay, using archived rules and decision time")
    replay.add_argument("snapshot")
    replay.add_argument("--min-pickup-gain", type=float)
    listing = sub.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    show = sub.add_parser("show", help="metadata and provenance, not the large raw bodies")
    show.add_argument("snapshot")
    backup = sub.add_parser("backup", help="consistent SQLite backup; do not copy only the live .sqlite3 file")
    backup.add_argument("destination", type=pathlib.Path)
    args = ap.parse_args(argv)
    try:
        if getattr(args, "min_pickup_gain", None) is not None:
            policies.PickupFloor(args.min_pickup_gain)
        store = archive.Archive(args.db, create=args.command == "collect")
        if args.command == "collect":
            snapshot_id = collector.collect(
                store, config.load(), week=args.week, refresh=args.refresh,
            )
            result = {
                "snapshot_id": snapshot_id, "status": "complete",
                "archive": str(store.path), "storage": store.stats(),
                **evaluation(store, snapshot_id, args.min_pickup_gain),
            }
        elif args.command == "replay":
            result = evaluation(store, args.snapshot, args.min_pickup_gain)
        elif args.command == "list":
            result = {"snapshots": store.list(args.limit), "storage": store.stats()}
        elif args.command == "show":
            result = store.manifest(args.snapshot)
        else:
            store.backup(args.destination)
            result = {"backup": str(args.destination)}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Snapshot operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
