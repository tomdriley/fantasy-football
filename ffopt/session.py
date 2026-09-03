"""Draft session state: the single source of truth behind the web interface.

Design decision that removes a whole class of bugs: a claim's **position in the
list is its pick number**, and the seat that made it is derived from that
position via the snake rule. Nothing stores "seat" or "is mine" alongside a
claim, so those facts cannot drift out of sync with the board. Correcting your
own seat instantly recomputes which players are yours, with no bookkeeping.

Three modes, differing only in where claims come from:

* ``live``     -- claims are pulled from the platform API.
* ``assisted`` -- claims are pulled from the API, but the operator may also add
                  or correct entries by hand when the feed lags behind the room.
* ``manual``   -- the operator enters every claim; the network is never touched.

Any mode can be used offline. If the feed fails while in a polling mode the
session records the failure and keeps working from what it already has, rather
than losing the draft.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import time
from typing import Iterable, Sequence

from . import availability, client, config, manual, optimizer, pool, season, shrinkage, valuation

MODES = ("live", "assisted", "manual")
STATE_PATH = config.REPO_ROOT / "data" / "session.json"

#: Shrinkage toward the market prior, matching the validated backtest setting.
DEFAULT_LAMBDA = 0.7
#: Rollout depth, matching the validated backtest setting.
DEFAULT_HORIZON = 8


class SessionError(ValueError):
    """Raised for operator errors that should be reported, not crash the app."""


@dataclasses.dataclass(slots=True)
class Claim:
    player_id: str
    source: str  # "api" | "manual"
    at: float = dataclasses.field(default_factory=time.time)


class DraftSession:
    def __init__(
        self,
        cfg: config.LeagueConfig | None = None,
        *,
        board: Sequence[pool.Item] | None = None,
        path: pathlib.Path | None = None,
    ):
        self.cfg = cfg or config.load()
        self.path = path or STATE_PATH
        self.mode = "live"
        self.seat: int | None = None
        self.claims: list[Claim] = []
        self.last_sync_error: str | None = None
        self.last_sync_at: float | None = None
        self._board: list[pool.Item] = list(board) if board is not None else []
        self._by_id: dict[str, pool.Item] = {}
        self._vor: dict[str, float] = {}
        self._tier_rank: dict[str, int] = {}
        if self._board:
            self._index_board()

    # -- board ----------------------------------------------------------
    def load_board(self, lam: float = DEFAULT_LAMBDA) -> int:
        """Build the item board. Uses the on-disk cache when offline."""
        items = pool.build(client.projections(self.cfg.season), self.cfg.scoring_weights)
        if lam:
            items = shrinkage.shrink(items, lam)
        self._board = availability.consensus_order(
            [i for i in items if i.adp is not None]
        )
        self._index_board()
        return len(self._board)

    def _index_board(self) -> None:
        self._by_id = {i.player_id: i for i in self._board}
        baselines = valuation.compute_baselines(self._board, self.cfg)
        self._vor = {
            i.player_id: valuation.value_over_replacement(i, baselines)
            for i in self._board
        }
        # Static fallback ordering for panic mode: value over replacement, but
        # with the two near-worthless types pushed to the end so a panicked
        # click can never spend an early pick on a kicker.
        deferred = {"K", "DEF"}
        ordered = sorted(
            self._board,
            key=lambda i: (i.pos in deferred, -self._vor.get(i.player_id, 0.0)),
        )
        self._tier_rank = {i.player_id: n for n, i in enumerate(ordered)}

    @property
    def board(self) -> list[pool.Item]:
        return self._board

    def item(self, player_id: str) -> pool.Item | None:
        return self._by_id.get(player_id)

    # -- derived state --------------------------------------------------
    @property
    def picks_made(self) -> int:
        return len(self.claims)

    @property
    def total_picks(self) -> int:
        return self.cfg.rounds * self.cfg.num_agents

    @property
    def claimed_ids(self) -> set[str]:
        return {c.player_id for c in self.claims}

    @property
    def current_pick(self) -> int:
        return min(self.picks_made + 1, self.total_picks + 1)

    @property
    def complete(self) -> bool:
        return self.picks_made >= self.total_picks

    def seat_on_clock(self) -> int | None:
        if self.complete:
            return None
        return self.cfg.seat_of_pick(self.current_pick)

    def is_my_turn(self) -> bool:
        return self.seat is not None and self.seat_on_clock() == self.seat

    def my_pick_numbers(self) -> list[int]:
        return self.cfg.pick_numbers(self.seat) if self.seat else []

    def my_roster(self) -> list[pool.Item]:
        """Players at *my* pick positions. Derived, never stored."""
        mine = set(self.my_pick_numbers())
        return [
            self._by_id[c.player_id]
            for n, c in enumerate(self.claims, 1)
            if n in mine and c.player_id in self._by_id
        ]

    def next_pick_number(self) -> int | None:
        for pick in self.my_pick_numbers():
            if pick >= self.current_pick:
                return pick
        return None

    def picks_until_my_turn(self) -> int | None:
        nxt = self.next_pick_number()
        return None if nxt is None else nxt - self.current_pick

    def unfilled_slots(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.my_roster():
            counts[item.pos] = counts.get(item.pos, 0) + 1
        return {
            position: slots - counts.get(position, 0)
            for position, slots in self.cfg.dedicated_slots.items()
            if slots - counts.get(position, 0) > 0
        }

    def available(self) -> list[pool.Item]:
        taken = self.claimed_ids
        return [i for i in self._board if i.player_id not in taken]

    # -- mutation -------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise SessionError(f"unknown mode {mode!r}")
        self.mode = mode
        self.save()

    def set_seat(self, seat: int | None) -> None:
        if seat is not None and not 1 <= seat <= self.cfg.num_agents:
            raise SessionError(f"seat must be 1..{self.cfg.num_agents}")
        self.seat = seat
        self.save()

    def claim(self, player_id: str, source: str = "manual") -> pool.Item:
        if player_id not in self._by_id:
            raise SessionError(f"unknown player {player_id!r}")
        if player_id in self.claimed_ids:
            raise SessionError(f"{self._by_id[player_id].name} is already claimed")
        if self.complete:
            raise SessionError("the draft is already complete")
        self.claims.append(Claim(player_id, source))
        self.save()
        return self._by_id[player_id]

    def undo(self) -> pool.Item | None:
        if not self.claims:
            return None
        claim = self.claims.pop()
        self.save()
        return self._by_id.get(claim.player_id)

    def correct(self, pick_no: int, player_id: str) -> pool.Item:
        """Replace the player recorded at `pick_no`, keeping every other pick."""
        self._check_pick(pick_no)
        if player_id not in self._by_id:
            raise SessionError(f"unknown player {player_id!r}")
        others = {c.player_id for n, c in enumerate(self.claims, 1) if n != pick_no}
        if player_id in others:
            raise SessionError(f"{self._by_id[player_id].name} is already claimed")
        self.claims[pick_no - 1] = Claim(player_id, "manual")
        self.save()
        return self._by_id[player_id]

    def insert(self, pick_no: int, player_id: str) -> pool.Item:
        """Insert a missed pick, shifting later picks down.

        This is the operation that rescues a session where one claim was never
        entered: without it every subsequent pick is attributed to the wrong
        seat, which silently corrupts whose roster is whose.
        """
        if not 1 <= pick_no <= self.picks_made + 1:
            raise SessionError(f"pick must be 1..{self.picks_made + 1}")
        if player_id not in self._by_id:
            raise SessionError(f"unknown player {player_id!r}")
        if player_id in self.claimed_ids:
            raise SessionError(f"{self._by_id[player_id].name} is already claimed")
        self.claims.insert(pick_no - 1, Claim(player_id, "manual"))
        self.save()
        return self._by_id[player_id]

    def remove(self, pick_no: int) -> pool.Item | None:
        """Delete a pick that never happened, shifting later picks up."""
        self._check_pick(pick_no)
        claim = self.claims.pop(pick_no - 1)
        self.save()
        return self._by_id.get(claim.player_id)

    def reset(self) -> None:
        self.claims = []
        self.save()

    def _check_pick(self, pick_no: int) -> None:
        if not 1 <= pick_no <= self.picks_made:
            raise SessionError(f"pick must be 1..{self.picks_made}")

    # -- syncing --------------------------------------------------------
    def propose(self) -> dict:
        """What the feed knows that we do not, *without* applying it.

        This is what separates assisted mode from live mode. In live mode the
        feed is applied automatically. In assisted mode the operator stays in
        control: the feed is a source of suggestions to accept, so a feed that
        is wrong, lagging, or disagreeing with the room cannot silently rewrite
        a board the operator has been maintaining by hand.
        """
        try:
            picks = client.draft_picks(self.cfg.draft_id)
        except Exception as exc:  # noqa: BLE001
            self.last_sync_error = str(exc)
            self.save()
            return {"ok": False, "error": str(exc), "additions": [], "conflicts": []}

        self.last_sync_error = None
        self.last_sync_at = time.time()
        remote = [
            str(p.get("player_id")) for p in picks if p.get("player_id")
        ]
        local = [c.player_id for c in self.claims]

        conflicts = [
            {
                "pick": n + 1,
                "local": self._brief_or_unknown(local[n]),
                "remote": self._brief_or_unknown(remote[n]),
            }
            for n in range(min(len(local), len(remote)))
            if local[n] != remote[n]
        ]
        additions = [
            {"pick": n + 1, **self._brief_or_unknown(pid)}
            for n, pid in enumerate(remote)
            if n >= len(local)
        ]
        self.save()
        return {
            "ok": True,
            "additions": additions,
            "conflicts": conflicts,
            "remote_total": len(remote),
            "local_total": len(local),
        }

    def accept_proposal(self) -> int:
        """Adopt the feed's view of the board wholesale."""
        result = self.sync()
        return self.picks_made if result.get("ok") else -1

    def sync(self) -> dict:
        """Pull claims from the platform feed.

        The feed is authoritative for picks it knows about. Manual entries
        beyond that point are preserved, because in assisted mode the operator
        is deliberately ahead of a lagging feed.
        """
        try:
            picks = client.draft_picks(self.cfg.draft_id)
        except Exception as exc:  # noqa: BLE001 - degrade, never crash mid-draft
            self.last_sync_error = str(exc)
            self.save()
            return {"ok": False, "error": str(exc), "picks": self.picks_made}

        self.last_sync_error = None
        self.last_sync_at = time.time()
        # Unknown players are kept, not dropped. The board only holds players
        # with a consensus rank, and a real draft will take players outside it
        # in late rounds. Dropping them would silently shorten the claim list,
        # and since a claim's position *is* its pick number, every later pick
        # would be attributed to the wrong seat -- corrupting whose roster is
        # whose without any visible symptom.
        remote = [
            Claim(str(p.get("player_id")), "api")
            for p in picks
            if p.get("player_id")
        ]
        if len(remote) >= len(self.claims):
            self.claims = remote
        else:
            # Keep the operator's manual entries that the feed has not caught up to.
            self.claims = remote + self.claims[len(remote):]
        if self.seat is None:
            self._infer_seat(picks)
        self.save()
        return {"ok": True, "picks": self.picks_made}

    def _infer_seat(self, picks: Iterable[dict]) -> None:
        for pick in picks:
            if str(pick.get("picked_by") or "") == self.cfg.my_user_id:
                slot = pick.get("draft_slot")
                if slot:
                    self.seat = int(slot)
                    return
        try:
            order = (client.draft(self.cfg.draft_id) or {}).get("draft_order") or {}
            if self.cfg.my_user_id in order:
                self.seat = int(order[self.cfg.my_user_id])
        except Exception:  # noqa: BLE001 - seat stays unknown; the UI will ask
            pass

    # -- advice ---------------------------------------------------------
    def panic(self, count: int = 5) -> list[dict]:
        """An instant, defensible pick with no simulation.

        Used when the clock is nearly out or the board is not trusted. It reads
        a precomputed static ordering, so it costs microseconds and cannot hang
        on the network. It returns several options because if the board state is
        wrong -- the very situation that triggers a panic -- the top choice may
        already be gone in the real room.
        """
        need = self.unfilled_slots()
        picks_left = len(
            [p for p in self.my_pick_numbers() if p >= self.current_pick]
        ) or self.cfg.rounds
        must_fill = need and picks_left <= sum(need.values())

        options = []
        for item in self.available():
            if must_fill and item.pos not in need:
                continue
            options.append(item)
            if len(options) > 400:
                break
        options.sort(key=lambda i: self._tier_rank.get(i.player_id, 1 << 30))
        return [self._brief(i) for i in options[:count]]

    def capture(self) -> dict:
        """An immutable snapshot of everything a recommendation needs.

        Taken under the service lock so the slow computation can then run
        *without* holding it. Otherwise a five-second simulation blocks the
        operator from recording a pick or hitting panic, which is unacceptable
        when panic exists precisely for the moment the clock is running out.
        """
        return {
            "available": self.available(),
            "roster": list(self.my_roster()),
            "seat": self.seat,
            "current_pick": self.current_pick,
        }

    def recommendations(
        self, trials: int = 30, count: int = 5, snapshot: dict | None = None
    ) -> list[dict]:
        """Full optimizer advice. Falls back to panic ordering on any failure."""
        if snapshot is not None:
            return self._recommend_from(snapshot, trials, count)
        return self._recommend_from(self.capture(), trials, count)

    def _recommend_from(self, snap: dict, trials: int, count: int) -> list[dict]:
        avail = snap["available"]
        seat = snap["seat"]
        if not avail or seat is None:
            return self.panic(count)
        try:
            baselines = valuation.compute_baselines(avail, self.cfg)
            vor = {
                i.player_id: valuation.value_over_replacement(i, baselines)
                for i in avail
            }
            recs = optimizer.recommend(
                snap["roster"], avail, self.cfg,
                seat=seat, current_pick=snap["current_pick"],
                vor=vor, waivers=season.objective_waivers(avail),
                trials=trials, horizon=DEFAULT_HORIZON,
                bot_seats=set(self.cfg.bot_seats()),
            )
        except Exception:  # noqa: BLE001 - advice must always be available
            return self.panic(count)

        out = []
        for rec in recs[:count]:
            brief = self._brief(rec.item)
            brief["ev"] = round(rec.expected_lineup_value, 1)
            out.append(brief)
        return out

    def _brief_or_unknown(self, player_id: str) -> dict:
        """Describe a claim even when the player is outside our board."""
        item = self._by_id.get(player_id)
        if item is not None:
            return self._brief(item)
        return {
            "player_id": player_id, "name": f"(unlisted player {player_id})",
            "pos": "?", "team": None, "adp": None, "vor": 0.0, "projected": 0.0,
        }

    def _brief(self, item: pool.Item) -> dict:
        return {
            "player_id": item.player_id,
            "name": item.name,
            "pos": item.pos,
            "team": item.team,
            "adp": round(item.adp, 1) if item.adp else None,
            "vor": round(self._vor.get(item.player_id, 0.0), 1),
            "projected": round(item.payoff, 1),
        }

    def search(self, query: str, limit: int = 8) -> list[dict]:
        match = manual.find(query, self.available())
        if match.resolved:
            return [self._brief(match.exact)]
        return [self._brief(i) for i in match.candidates[:limit]]

    def find_claimed(self, query: str) -> pool.Item | None:
        """Whether a query names a player who has *already* been claimed.

        Searching only the available pool means a duplicate entry reports "no
        match", which under time pressure reads as a typo and invites the
        operator to retype it. Distinguishing the two cases is the difference
        between a clear message and a confusing one.
        """
        taken = [i for i in self._board if i.player_id in self.claimed_ids]
        if not taken:
            return None
        match = manual.find(query, taken)
        if match.resolved:
            return match.exact
        return match.candidates[0] if len(match.candidates) == 1 else None

    # -- persistence ----------------------------------------------------
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "draft_id": self.cfg.draft_id,
            "mode": self.mode,
            "seat": self.seat,
            "claims": [[c.player_id, c.source, c.at] for c in self.claims],
        }
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f)
        tmp.replace(self.path)

    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        if data.get("draft_id") != self.cfg.draft_id:
            return False
        self.mode = data.get("mode", "live")
        self.seat = data.get("seat")
        self.claims = [
            Claim(pid, src, at) for pid, src, at in data.get("claims", [])
        ]
        return True

    # -- view -----------------------------------------------------------
    def snapshot(self) -> dict:
        """Everything the interface needs, in one payload."""
        return {
            "mode": self.mode,
            "seat": self.seat,
            "picks_made": self.picks_made,
            "total_picks": self.total_picks,
            "current_pick": self.current_pick,
            "round": min((self.current_pick - 1) // self.cfg.num_agents + 1, self.cfg.rounds),
            "seat_on_clock": self.seat_on_clock(),
            "my_turn": self.is_my_turn(),
            "complete": self.complete,
            "picks_until_my_turn": self.picks_until_my_turn(),
            "next_pick_number": self.next_pick_number(),
            "roster": [self._brief(i) for i in self.my_roster()],
            "unfilled": self.unfilled_slots(),
            "starting_slots": self.cfg.starting_slots,
            "rounds": self.cfg.rounds,
            "num_agents": self.cfg.num_agents,
            "bot_seats": self.cfg.bot_seats(),
            "pick_timer": self.cfg.pick_timer_seconds,
            "last_sync_error": self.last_sync_error,
            "last_sync_at": self.last_sync_at,
            "board_size": len(self._board),
            "assisted": self.mode == "assisted",
            "recent": [
                {
                    "pick": n,
                    "seat": self.cfg.seat_of_pick(n),
                    "source": c.source,
                    **self._brief_or_unknown(c.player_id),
                }
                for n, c in list(enumerate(self.claims, 1))[-12:]
            ],
        }
