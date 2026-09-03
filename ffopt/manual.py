"""Manual board entry, for when the live claim feed is unavailable.

The architecture lists manual entry as the degradation path if the pick feed
fails, and without it a network problem at 4pm means falling back to paper.
This module supplies it.

The binding constraint is the 60 second timer. Typing a full name is too slow
and too error prone under pressure, so lookup is fuzzy and forgiving: a surname
alone is normally enough, and team defenses match on their city or nickname.
Every entry echoes what it matched, and any entry can be undone, because a
silent mismatch corrupts the board for the rest of the draft.

The board is persisted after every change, so a crash or an accidental
Ctrl-C does not lose a draft that is already in progress.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import pathlib
from typing import Iterable, Sequence

from . import config, pool

STATE_PATH = config.REPO_ROOT / "data" / "manual-board.json"

#: Consensus rank beyond which an item will realistically never be claimed.
#: A 10-agent, 15-round draft consumes 150 items; the margin covers late runs.
PLAUSIBLE_ADP = 220


@dataclasses.dataclass(slots=True)
class Match:
    """Result of a lookup: either resolved, ambiguous, or empty."""

    exact: pool.Item | None
    candidates: list[pool.Item]

    @property
    def resolved(self) -> bool:
        return self.exact is not None

    @property
    def ambiguous(self) -> bool:
        return self.exact is None and len(self.candidates) > 1

    @property
    def empty(self) -> bool:
        return self.exact is None and not self.candidates


def _norm(text: str) -> str:
    keep = [c.lower() for c in text if c.isalnum() or c.isspace()]
    return " ".join("".join(keep).split())


def _surname(item: pool.Item) -> str:
    parts = _norm(item.name).split()
    return parts[-1] if parts else ""


def find(query: str, items: Sequence[pool.Item]) -> Match:
    """Locate an item from a partial, possibly misspelt name.

    Resolution order is chosen for speed of typing rather than generality:
    exact name, then unique surname, then surname prefix, then initial plus
    surname, then a spelling-tolerant fallback. A team defense also matches on
    any word of its name, so "rams" or "seahawks" both work.
    """
    q = _norm(query)
    if not q:
        return Match(None, [])

    def uniq(matches: list[pool.Item]) -> Match | None:
        if len(matches) == 1:
            return Match(matches[0], matches)
        return None

    exact = [i for i in items if _norm(i.name) == q]
    if (m := uniq(exact)):
        return m
    if exact:
        return Match(None, exact)

    surname = [i for i in items if _surname(i) == q]
    if (m := uniq(surname)):
        return m

    # Team defenses carry a city and a nickname; allow either.
    team = [
        i for i in items
        if i.pos == "DEF" and q in _norm(i.name).split()
    ]
    if (m := uniq(team)):
        return m

    prefix = [i for i in items if _surname(i).startswith(q)]
    if (m := uniq(prefix)):
        return m

    # "j gibbs" style: leading initial plus surname.
    parts = q.split()
    if len(parts) == 2 and len(parts[0]) == 1:
        initial = [
            i for i in items
            if _surname(i) == parts[1] and _norm(i.name).startswith(parts[0])
        ]
        if (m := uniq(initial)):
            return m

    full = [i for i in items if q in _norm(i.name)]
    if (m := uniq(full)):
        return m

    pool_names = {_surname(i): i for i in items}
    close = difflib.get_close_matches(q, list(pool_names), n=5, cutoff=0.75)
    fuzzy = [pool_names[c] for c in close]

    candidates = surname or team or prefix or full or fuzzy
    candidates = sorted(candidates, key=lambda i: (i.adp if i.adp is not None else 9e9))

    # Most surname collisions are between one draftable player and several who
    # will never be claimed -- "nacua" is Puka (consensus 4) or Samson (never).
    # Reporting that as ambiguous wastes seconds we do not have, so resolve to
    # the sole plausible candidate when there is exactly one.
    plausible = [
        i for i in candidates
        if i.adp is not None and i.adp <= PLAUSIBLE_ADP
    ]
    if len(plausible) == 1:
        return Match(plausible[0], candidates)
    if plausible:
        return Match(None, plausible[:6])
    return Match(None, candidates[:6])


def suggest(query: str, items: Sequence[pool.Item], limit: int = 8) -> list[pool.Item]:
    """Ranked candidates for a partial name, *never* collapsed to one.

    `find` deliberately resolves to a single item when only one candidate is
    plausibly draftable, because a terminal prompt has no room to show a list.
    A clickable list does have room, and hiding the alternatives is what makes
    a wrong guess expensive: typing "smith" returned exactly one Smith, so an
    operator who meant a different one had to notice the wrong name, undo it
    and retype -- inside a 60-second window where a dozen picks might need
    recording.

    Showing every match costs nothing and makes a wrong entry hard to commit by
    accident, because the operator clicks a name they can see. Ordering is by
    market consensus, so the likeliest player is first and the undraftable
    namesakes are below rather than absent.
    """
    q = _norm(query)
    if not q:
        return []

    scored: dict[str, tuple[int, float, pool.Item]] = {}

    def add(rank: int, found: Sequence[pool.Item]) -> None:
        for i in found:
            key = i.player_id or i.name
            adp = i.adp if i.adp is not None else 9e9
            if key not in scored or rank < scored[key][0]:
                scored[key] = (rank, adp, i)

    add(0, [i for i in items if _norm(i.name) == q])
    add(1, [i for i in items if _surname(i) == q])
    add(2, [i for i in items if i.pos == "DEF" and q in _norm(i.name).split()])
    add(3, [i for i in items if _surname(i).startswith(q)])

    parts = q.split()
    if len(parts) == 2 and len(parts[0]) == 1:
        add(3, [
            i for i in items
            if _surname(i) == parts[1] and _norm(i.name).startswith(parts[0])
        ])

    add(4, [i for i in items if any(w.startswith(q) for w in _norm(i.name).split())])
    add(5, [i for i in items if q in _norm(i.name)])

    if not scored:
        by_surname = {_surname(i): i for i in items}
        close = difflib.get_close_matches(q, list(by_surname), n=limit, cutoff=0.7)
        add(6, [by_surname[c] for c in close])

    ranked = sorted(scored.values(), key=lambda t: (t[0], t[1]))
    return [item for _, _, item in ranked[:limit]]


class ManualBoard:
    """A locally maintained record of which items have been claimed."""

    def __init__(self, cfg: config.LeagueConfig, path: pathlib.Path | None = None):
        self.cfg = cfg
        self.path = path or STATE_PATH
        self.claims: list[tuple[str, bool]] = []  # (player_id, is_mine)

    # -- mutation -------------------------------------------------------
    def claim(self, item: pool.Item, mine: bool = False) -> None:
        self.claims.append((item.player_id, mine))
        self.save()

    def undo(self) -> str | None:
        if not self.claims:
            return None
        pid, _ = self.claims.pop()
        self.save()
        return pid

    # -- queries --------------------------------------------------------
    @property
    def claimed(self) -> set[str]:
        return {pid for pid, _ in self.claims}

    @property
    def picks_made(self) -> int:
        return len(self.claims)

    def my_roster(self, items: Iterable[pool.Item]) -> list[pool.Item]:
        by_id = {i.player_id: i for i in items}
        return [by_id[pid] for pid, mine in self.claims if mine and pid in by_id]

    def seat_on_clock(self) -> int:
        return (self.picks_made % self.cfg.num_agents) + 1

    # -- persistence ----------------------------------------------------
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"draft_id": self.cfg.draft_id, "claims": self.claims}, f)
        tmp.replace(self.path)

    def load(self) -> bool:
        """Restore a board saved earlier in this same draft."""
        if not self.path.exists():
            return False
        with open(self.path) as f:
            data = json.load(f)
        if data.get("draft_id") != self.cfg.draft_id:
            return False
        self.claims = [(pid, bool(mine)) for pid, mine in data.get("claims", [])]
        return True

    def reset(self) -> None:
        self.claims = []
        self.save()


def sync_from_feed(board: ManualBoard, picks: Sequence[dict], my_user_id: str) -> int:
    """Seed a manual board from whatever the feed already returned.

    If the feed fails partway through a draft, the picks already retrieved are
    still good. Starting manual entry from there avoids retyping the board.
    """
    board.claims = [
        (str(p.get("player_id") or ""), str(p.get("picked_by") or "") == my_user_id)
        for p in picks
        if p.get("player_id")
    ]
    board.save()
    return len(board.claims)
