#!/usr/bin/env python3
"""Generate the printable draft sheet for the current season."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffopt import cheatsheet, client, config, pool


def main() -> int:
    cfg = config.load()
    items = pool.build(client.projections(cfg.season), cfg.scoring_weights)
    board = [i for i in items if i.adp is not None]
    text = cheatsheet.render(board, cfg)
    out = pathlib.Path(__file__).resolve().parent.parent / "draft-sheet.txt"
    out.write_text(text + "\n")
    print(text)
    print(f"\nwritten to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
