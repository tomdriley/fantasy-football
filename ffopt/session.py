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
import os
import pathlib
import random
import time
from typing import Iterable, Sequence

from . import availability, client, config, manual, optimizer, pool, season, shrinkage, valuation

MODES = ("live", "assisted", "manual")

#: Where the live board is persisted. Overridable so a rehearsal cannot write
#: over the state of a draft that is actually in progress.
STATE_PATH = pathlib.Path(
    os.environ.get("FFOPT_SESSION_PATH", config.REPO_ROOT / "data" / "session.json")
)

#: Shrinkage toward the market prior, matching the validated backtest setting.
DEFAULT_LAMBDA = 0.7
#: Rollout depth, matching the validated backtest setting.
DEFAULT_HORIZON = 8

#: How old a saved board may be and still be treated as a draft in progress.
#: A draft lasts under an hour and is resumed within seconds of a crash, so
#: anything older is leftover state rather than something to restore.
STATE_MAX_AGE = 6 * 3600

#: Rollout samples per candidate for live advice.
#:
#: The backtest uses 30, which is ample when averaging 200 drafts: the sampling
#: error cancels out. A live draft is a single sample, so it does not cancel --
#: and at 30 the top recommendation on an opening board was measured flipping
#: between a 144-value RB and a 43-value QB across seeds, because their
#: estimated values sit within two points of each other. Seeds agreed
#: completely from 60 upward; 120 is double the measured convergence point and
#: still an order of magnitude inside the pick timer.
LIVE_TRIALS = 120


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
        #: Manual is the default because it is the only mode that cannot be
        #: wrong about the board. Live polling is an optimisation the operator
        #: opts into once they have seen it working; defaulting to it means a
        #: silent feed failure or a mis-attributed pick corrupts the board
        #: before anyone has confirmed the tool is even pointed at the right
        #: draft.
        self.mode = "manual"
        #: False until the operator has been through the setup screen. Guessing
        #: a seat is never acceptable -- it determines the entire pick schedule
        #: -- so the interface asks rather than assumes.
        self.configured = False
        self.seat: int | None = None
        #: How self.seat was determined: "picks" (a pick attributed to us),
        #: "draft_order" (the published draw), or "manual" (the operator's
        #: word, never overridden). None means genuinely unknown -- which is
        #: reported rather than guessed at.
        self.seat_source: str | None = None
        self.claims: list[Claim] = []
        self.last_sync_error: str | None = None
        self.last_sync_at: float | None = None
        self.stale_state_reason: str | None = None
        self._board: list[pool.Item] = list(board) if board is not None else []
        self._by_id: dict[str, pool.Item] = {}
        self._vor: dict[str, float] = {}
        self._tier_rank: dict[str, int] = {}
        self._market_order: list[pool.Item] = []
        self._short: dict[str, str] = {}
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
        # Market order, for the click-to-record grid. The board is already
        # consensus-ordered in production, but sorting here means the grid does
        # not silently depend on how the board happened to be constructed.
        self._market_order = sorted(
            self._board,
            key=lambda i: i.adp if i.adp is not None else 9e9,
        )
        self._index_short_names()

    def _index_short_names(self) -> None:
        """Precompute the display name for every item.

        Short names ("J. Gibbs") are faster to match against the platform's own
        listing, but two draftable players can collapse onto one: Bijan and
        Brian Robinson are both running backs and both get picked. Recording
        the wrong one is exactly the error this format is meant to prevent, so
        a name that is ambiguous falls back to the full version.

        Ambiguity is judged only among players who might actually be claimed.
        Measured against the whole 3300-player pool, a quarter of the top 100
        would collide with someone unpickable and lose the short form for no
        reason; restricted to the draftable range it is four names.
        """
        live = [
            i for i in self._board
            if i.adp is not None and i.adp <= manual.PLAUSIBLE_ADP
        ]
        seen: dict[str, int] = {}
        for item in live:
            key = pool.short_name(item.name, item.pos)
            seen[key] = seen.get(key, 0) + 1
        ambiguous = {k for k, n in seen.items() if n > 1}

        self._short = {}
        for item in self._board:
            key = pool.short_name(item.name, item.pos)
            self._short[item.player_id] = item.name if key in ambiguous else key

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
        self.seat_source = "manual" if seat is not None else None
        self.save()

    def start(self, seat: int | None, mode: str) -> dict:
        """Apply the setup screen's answers in one step.

        Seat and mode are set together because they are one decision: the
        operator is telling us where they sit and how the board will be kept.
        Doing it atomically means there is no in-between state where the tool
        is polling a live feed while still believing it holds a seat it does
        not, which is exactly when picks get attributed to the wrong roster.
        """
        if mode not in MODES:
            raise SessionError(f"unknown mode {mode!r}")
        if seat is not None and not 1 <= seat <= self.cfg.num_agents:
            raise SessionError(f"seat must be 1..{self.cfg.num_agents}")
        self.seat = seat
        self.seat_source = "manual" if seat is not None else None
        self.mode = mode
        self.configured = True
        self.save()
        return {"seat": self.seat, "mode": self.mode}

    def go_manual(self) -> dict:
        """Drop to manual entry immediately, from any state.

        The escape hatch: whatever the feed is doing, stop listening to it and
        let the operator drive. Deliberately does not touch the board, so it is
        safe to hit at any moment -- it only changes where future picks come
        from, never what has already been recorded.
        """
        self.mode = "manual"
        self.save()
        return {"mode": self.mode}

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
        """Clear the board, keeping the operator's setup.

        Seat and mode are deliberately preserved: resetting is what you do when
        the *picks* are wrong, and being asked to re-enter who you are while a
        draft clock is running would be a needless second problem. Use the
        setup screen to change identity.
        """
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

    def alignment(self, local_ids: list[str] | None = None) -> dict:
        """Is our board still in step with the room? Changes nothing.

        The failure this exists for: a missed or duplicated entry shifts every
        later pick by one, so the tool believes players are available who are
        gone and attributes picks to the wrong rosters. Nothing about that is
        visible -- the board still looks orderly -- and every recommendation
        after it is wrong. It is only noticed on our own turn, by which point
        several picks have been made on bad advice.

        Checked in *every* mode, including manual. Manual mode exists so the
        operator is not at the mercy of the feed, but reading the feed to ask
        "do we agree?" costs nothing and never writes. Offline it degrades to
        reporting that it does not know, which is honest and still lets the
        pick label be eyeballed against the platform.
        """
        local_ids = (
            list(local_ids) if local_ids is not None
            else [c.player_id for c in self.claims]
        )
        local = len(local_ids)
        out = {
            "local_total": local,
            "local_label": self.cfg.pick_label(min(local + 1, self.total_picks)),
            "checked": False,
            "aligned": None,
            "drift": 0,
            "first_conflict": None,
        }
        try:
            picks = client.draft_picks(self.cfg.draft_id)
        except Exception as exc:  # noqa: BLE001 - offline is a normal state
            out["error"] = str(exc)
            return out

        remote_ids = [str(p.get("player_id")) for p in picks if p.get("player_id")]
        remote = len(remote_ids)
        first = next(
            (
                n + 1 for n in range(min(len(local_ids), remote))
                if local_ids[n] != remote_ids[n]
            ),
            None,
        )
        out.update({
            "checked": True,
            "remote_total": remote,
            "remote_label": self.cfg.pick_label(min(remote + 1, self.total_picks)),
            "drift": local - remote,
            "first_conflict": first,
            "first_conflict_label": self.cfg.pick_label(first) if first else None,
            "aligned": local == remote and first is None,
        })
        return out

    def adopt_feed(self) -> dict:
        """Replace the board with the feed's, wholesale.

        The recovery action for a board that has drifted. Unlike `sync`, this
        does *not* preserve manual entries beyond the feed: entries past the
        feed's end are exactly what a mis-entry looks like, and keeping them is
        what let the drift persist. The feed is the room's own record, so when
        the two disagree the feed wins.
        """
        try:
            picks = client.draft_picks(self.cfg.draft_id)
        except Exception as exc:  # noqa: BLE001
            self.last_sync_error = str(exc)
            self.save()
            return {"ok": False, "error": str(exc)}

        before = self.picks_made
        self.claims = [
            Claim(str(p.get("player_id")), "api")
            for p in picks if p.get("player_id")
        ]
        self.last_sync_error = None
        self.last_sync_at = time.time()
        if self.seat is None:
            self._infer_seat(picks)
        self.save()
        return {
            "ok": True,
            "before": before,
            "after": self.picks_made,
            "changed": self.picks_made - before,
        }

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

    def published_seat(self) -> int | None:
        """Our seat according to the published draft order, or None.

        Read-only and never applied automatically -- it pre-fills the setup
        screen so the operator confirms a number rather than hunting for it.
        The order is often undrawn until minutes before the start, so this
        returns None just as legitimately as it returns a seat.
        """
        try:
            order = (client.draft(self.cfg.draft_id) or {}).get("draft_order") or {}
        except Exception:  # noqa: BLE001 - offline: the operator types it
            return None
        value = order.get(self.cfg.my_user_id)
        return int(value) if value else None

    def _infer_seat(self, picks: Iterable[dict]) -> None:
        """Determine our seat, from authoritative evidence only.

        Two sources, both of which state the answer rather than imply it:

        1. A pick already attributed to us carries its own ``draft_slot``.
           This is ground truth and cannot be wrong.
        2. ``draft_order`` maps user ids to seats once the order is drawn.

        Nothing else is used. In particular ``slot_to_roster_id`` is *not* a
        seat assignment: before the draw it is an identity mapping of slot to
        roster id, so reading a seat out of it would be inventing a draft
        order that does not exist yet. Guessing here is the worst possible
        error, because the seat determines the entire pick schedule -- every
        recommendation would be optimised for a position we do not hold, and
        it would look confident while doing it.

        Staying unknown is the correct outcome until the platform says
        otherwise, so the operator is asked instead.
        """
        if self.seat_source in ("picks", "manual"):
            return

        for pick in picks:
            if str(pick.get("picked_by") or "") == self.cfg.my_user_id:
                slot = pick.get("draft_slot")
                if slot:
                    self.seat, self.seat_source = int(slot), "picks"
                    return

        try:
            order = (client.draft(self.cfg.draft_id) or {}).get("draft_order") or {}
        except Exception:  # noqa: BLE001 - seat stays unknown; the UI will ask
            return
        if self.cfg.my_user_id in order:
            self.seat, self.seat_source = int(order[self.cfg.my_user_id]), "draft_order"

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

    def _advice_seed(self, snap: dict) -> str:
        """A seed that depends only on the board, never on the clock.

        The rollout is a Monte Carlo estimate, so an unseeded run gives a
        slightly different answer each time. On a board where the top two
        candidates are within a point or two that is enough to reorder them,
        and the operator sees the recommendation change while nothing about
        the draft has. Deriving the seed from the position means the same board
        always produces the same advice, and a genuinely new board produces a
        genuinely independent estimate.
        """
        taken = ",".join(c.player_id for c in self.claims)
        return f"{snap['seat']}:{snap['current_pick']}:{taken}"

    def recommendations(
        self, trials: int = LIVE_TRIALS, count: int = 5, snapshot: dict | None = None
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
                rng=random.Random(self._advice_seed(snap)),
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
            "short": f"(unlisted {player_id})",
            "pos": "?", "team": None, "adp": None, "vor": 0.0, "projected": 0.0,
        }

    def _brief(self, item: pool.Item) -> dict:
        return {
            "player_id": item.player_id,
            "name": item.name,
            "short": self._short.get(item.player_id) or item.name,
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

    def suggest(self, query: str, limit: int = 8) -> list[dict]:
        """Every plausible match, ranked. Drives the click-to-record list."""
        return [self._brief(i) for i in manual.suggest(query, self.available(), limit)]

    def quick_board(self, limit: int = 18) -> list[dict]:
        """The players most likely to be taken next, in market order.

        Recording an opponent's pick is a search problem only when the pick is
        surprising. Measured against a realistic field, the next player claimed
        is inside the top 15 of this list 86% of the time and the top 5 76% of
        the time -- so most picks need no typing at all, just a click.

        That matters because the picks between two of our own turns can arrive
        in a burst: nine opponents autopicking take seconds, and all of them
        have to be recorded before our own clock is meaningful.
        """
        taken = self.claimed_ids
        out = []
        for item in self._market_order:
            if item.player_id in taken:
                continue
            out.append(self._brief(item))
            if len(out) >= limit:
                break
        return out

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
            "api_base": client.API_V1,
            "saved_at": time.time(),
            "mode": self.mode,
            "seat": self.seat,
            "seat_source": self.seat_source,
            "configured": self.configured,
            "claims": [[c.player_id, c.source, c.at] for c in self.claims],
        }
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f)
        tmp.replace(self.path)

    def load(self, max_age: float = STATE_MAX_AGE) -> bool:
        """Restore a board saved earlier in *this* draft.

        Three guards, each closing a way a stale file could silently seed a
        fresh session with picks that never happened:

        * the draft id must match, so another league's board is never adopted;
        * the API base must match, so a board built against a mock draft server
          is never restored into a session pointed at the real platform;
        * the file must be recent, because a board hours old is a leftover from
          testing rather than a draft in progress -- a draft lasts under an hour
          and is resumed within seconds of an interruption, never the next day.
        """
        if not self.path.exists():
            return False
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        if data.get("draft_id") != self.cfg.draft_id:
            return False
        if data.get("api_base") != client.API_V1:
            self.stale_state_reason = "saved against a different API (mock vs live)"
            return False
        age = time.time() - float(data.get("saved_at") or 0)
        if max_age and age > max_age:
            self.stale_state_reason = (
                "saved %.1f hours ago; treated as leftover, not a draft in progress"
                % (age / 3600)
            )
            return False
        self.mode = data.get("mode", "manual")
        self.seat = data.get("seat")
        self.seat_source = data.get("seat_source")
        self.configured = bool(data.get("configured"))
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
            "seat_source": self.seat_source,
            "configured": self.configured,
            "picks_made": self.picks_made,
            "total_picks": self.total_picks,
            "current_pick": self.current_pick,
            # The platform's own notation, so the two boards can be compared
            # without translating between numbering schemes.
            "pick_label": self.cfg.pick_label(self.current_pick),
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
            "quick": self.quick_board(),
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
