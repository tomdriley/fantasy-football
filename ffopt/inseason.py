"""Read-only weekly advice from an explicit, timestamped league snapshot."""

from __future__ import annotations

import dataclasses
import time
from typing import Iterable, Sequence

from . import client, config, lineup, scoring

STREAM_POSITIONS = ("DEF", "K")


class StateError(ValueError):
    """The observed state is not safe to use for actionable advice."""


@dataclasses.dataclass(slots=True)
class WeekState:
    season: str
    week: int
    my_roster_id: int
    my_player_ids: list[str]
    my_starters: list[str]
    rostered_elsewhere: set[str]
    projections: dict[str, float]
    players: dict[str, dict]
    kickoffs: dict[str, int]
    fetched_at_ms: int
    league_id: str = ""
    reserve: set[str] = dataclasses.field(default_factory=set)
    transactions: list[dict] = dataclasses.field(default_factory=list)
    rosters: list[dict] = dataclasses.field(default_factory=list)
    matchups: list[dict] = dataclasses.field(default_factory=list)
    games: list[dict] = dataclasses.field(default_factory=list)
    sources: dict[str, int] = dataclasses.field(default_factory=dict)
    move_warnings: list[str] = dataclasses.field(default_factory=list)
    unavailable_teams: set[str] = dataclasses.field(default_factory=set)
    started_teams: set[str] = dataclasses.field(default_factory=set)
    snapshot_id: str | None = None

    def team_of(self, player_id: str) -> str | None:
        return (self.players.get(player_id) or {}).get("team")

    def on_bye(self, player_id: str) -> bool:
        team = self.team_of(player_id)
        return bool(team and team not in self.kickoffs and team not in self.unavailable_teams)

    def kickoff_ms(self, player_id: str) -> int | None:
        return self.kickoffs.get(self.team_of(player_id))

    def is_locked(self, player_id: str, *, now_ms: int | None = None) -> bool:
        kickoff = self.kickoff_ms(player_id)
        now = self.fetched_at_ms if now_ms is None else now_ms
        return self.team_of(player_id) in self.started_teams or (
            kickoff is not None and now >= kickoff
        )

    def is_free(self, player_id: str) -> bool:
        """Unowned, NOT necessarily available for immediate free agency."""
        return player_id not in self.rostered_elsewhere and player_id not in self.my_player_ids


def _projection_map(records: Iterable[dict], weights: dict[str, float]) -> dict[str, float]:
    out = {}
    for rec in records:
        stats = rec.get("stats") or {}
        pid = str(rec.get("player_id") or "")
        # An ADP-only record is missing data, not a forecast of zero points.
        if pid and any(k in stats for k in weights):
            out[pid] = scoring.payoff(stats, weights)
    return out


def _kickoffs(games: Iterable[dict]) -> dict[str, int]:
    out = {}
    for game in games:
        meta = game.get("metadata") or {}
        start = game.get("start_time")
        if not isinstance(start, int) or start <= 0:
            raise StateError(f"missing kickoff time for game {game.get('game_id')}")
        for side in ("home_team", "away_team"):
            team = meta.get(side)
            if not team or team in out:
                raise StateError("incomplete or duplicate team schedule")
            out[team] = start
    if not out:
        raise StateError("empty schedule; cannot distinguish bye weeks from missing data")
    return out


def _roster_players(roster: dict) -> set[str]:
    return set(roster.get("players") or []) | set(roster.get("reserve") or []) | set(
        roster.get("taxi") or []
    )


def load(
    cfg: config.LeagueConfig, week: int, *, season: str | None = None,
    refresh: bool = False,
) -> WeekState:
    season = str(season or cfg.season)
    nfl = client.nfl_state()
    if season != str(nfl.get("season")) or week != nfl.get("week"):
        raise StateError("live roster advice supports only the current NFL season/week")
    league = client.get(
        client._uncached(f"{client.API_V1}/league/{cfg.league_id}"),
        f"weekly_league_{cfg.league_id}", ttl=client.LIVE_TTL,
        allow_stale=False, refresh=refresh,
    )
    records = client.weekly_projections(season, week, refresh=refresh)
    data = {
        "nfl_state": nfl,
        "league": league,
        "rosters": client.rosters(cfg.league_id, refresh=refresh),
        "matchups": client.matchups(cfg.league_id, week, refresh=refresh),
        "projections": records,
        "players": client.players(refresh=refresh),
        "scores": client.scores(season, week, refresh=refresh),
        "transactions": client.transactions(cfg.league_id, week, refresh=refresh),
    }
    if week > 1:
        data["previous_transactions"] = client.transactions(cfg.league_id, week - 1, refresh=refresh)
    keys = (
        f"wproj_{season}_{week}", "players_nfl", f"scores_{season}_{week}",
        f"rosters_{cfg.league_id}", f"matchups_{cfg.league_id}_{week}",
        f"transactions_{cfg.league_id}_{week}", f"weekly_league_{cfg.league_id}",
    )
    sources = {
        key: int(client._cache_path(key).stat().st_mtime * 1000) for key in keys
    }
    return build_state(
        cfg, week, data, observed_at_ms=int(time.time() * 1000), sources=sources,
    )


def build_state(
    cfg: config.LeagueConfig, week: int, data: dict, *,
    observed_at_ms: int, sources: dict[str, int] | None = None,
    snapshot_id: str | None = None,
) -> WeekState:
    """Pure assembly shared by the live advisor and offline snapshot replay.

    The supplied clock, configuration and payloads are the ONLY inputs. Do not
    introduce current time, a cache lookup, or a network call in this function.
    """
    objects = ("nfl_state", "league", "players")
    arrays = ("rosters", "matchups", "projections", "scores", "transactions")
    if week > 1:
        arrays += ("previous_transactions",)
    for role in objects:
        if not isinstance(data.get(role), dict):
            raise StateError(f"missing or malformed {role} object")
    if any(not isinstance(meta, dict) for meta in data["players"].values()):
        raise StateError("malformed player map entry")
    for role in arrays:
        if not isinstance(data.get(role), list) or any(not isinstance(r, dict) for r in data[role]):
            raise StateError(f"missing or malformed {role} array")
    nfl, league = data["nfl_state"], data["league"]
    season = str(cfg.season)
    if season != str(nfl.get("season")) or week != nfl.get("week"):
        raise StateError("snapshot season/week does not match its captured NFL state")
    if (
        str(league.get("season")) != season
        or league.get("status") != "in_season"
        or league.get("scoring_settings") != cfg.scoring_weights
        or league.get("roster_positions") != cfg.raw["roster_constraints"]["roster_positions_ordered"]
    ):
        raise StateError("league rules changed; run python3 scripts/refresh_rules.py")
    saved_settings = cfg.in_season.get("raw_settings")
    if saved_settings is None:
        raise StateError("in-season rules not captured; run python3 scripts/refresh_rules.py")
    live_settings = league.get("settings") or {}
    for key in saved_settings:
        if live_settings.get(key) != saved_settings[key]:
            raise StateError(f"league setting {key} changed; run scripts/refresh_rules.py")
    rosters = data["rosters"]
    if len(rosters) != cfg.num_agents:
        raise StateError("incomplete league rosters; cannot determine player ownership")
    owned = [r for r in rosters if r.get("owner_id") == cfg.my_user_id]
    if len(owned) != 1:
        raise StateError("could not uniquely identify your roster; refresh league rules")
    mine = owned[0]
    occupied: set[str] = set()
    for r in rosters:
        if "players" not in r or "roster_id" not in r:
            raise StateError("incomplete roster record; ownership is unknown")
        players = _roster_players(r)
        if occupied & players:
            raise StateError("a player appears on multiple rosters; refresh and retry")
        occupied |= players
    my_ids = _roster_players(mine)
    matches = data["matchups"]
    match = next((m for m in matches if m["roster_id"] == mine["roster_id"]), None)
    if match is None or len(match.get("starters") or []) != len(cfg.starting_positions):
        raise StateError("missing ordered matchup starters; cannot preserve locked slots")
    starters = [str(p) if p else "0" for p in match["starters"]]
    chosen = [p for p in starters if p != "0"]
    if len(set(chosen)) != len(chosen) or not set(chosen) <= my_ids:
        raise StateError("rosters and starters disagree; refresh and retry")
    records = data["projections"]
    projections = _projection_map(records, cfg.scoring_weights)
    if not projections:
        raise StateError("weekly projections are empty")
    players = data["players"]
    if not my_ids <= players.keys():
        raise StateError("rostered player missing from player map; refresh before advising")
    games = data["scores"]
    kickoffs = _kickoffs(games)
    tx = list(data["transactions"]) + list(data.get("previous_transactions", []))
    state = WeekState(
        season, week, mine["roster_id"], sorted(my_ids), starters, occupied - my_ids,
        projections, players, kickoffs, observed_at_ms,
        league_id=cfg.league_id, reserve=set(mine.get("reserve") or []),
        transactions=tx, rosters=rosters, matchups=matches, games=games,
        sources=dict(sources or {}), snapshot_id=snapshot_id,
    )
    for game in games:
        meta = game.get("metadata") or {}
        teams = {meta["home_team"], meta["away_team"]}
        if meta.get("canceled") or game.get("status") in ("canceled", "postponed"):
            state.unavailable_teams |= teams
        if meta.get("has_started") or game.get("status") in ("in_progress", "complete", "final"):
            state.started_teams |= teams
    if live_settings.get("disable_adds"):
        state.move_warnings.append("league additions are disabled")
    if len(my_ids - state.reserve) > cfg.roster_size:
        state.move_warnings.append("active roster exceeds capacity; repair it in Sleeper")
    if len(state.reserve) > cfg.reserve_slots:
        state.move_warnings.append("reserve exceeds capacity; repair it in Sleeper")
    allows = cfg.in_season["reserve"]["allows"]
    status_flags = {
        "Out": "reserve_allow_out", "Doubtful": "reserve_allow_doubtful",
        "NA": "reserve_allow_na", "Sus": "reserve_allow_sus",
        "COV": "reserve_allow_cov", "DNR": "reserve_allow_dnr",
    }
    for pid in state.reserve:
        status = players[pid].get("injury_status")
        if status != "IR" and not allows.get(status_flags.get(status)):
            state.move_warnings.append(
                f"confirm IR eligibility/activate {players[pid].get('full_name', pid)} "
                f"({status or 'no designation'}) before adding players"
            )
    return state


def _position(meta: dict) -> str | None:
    positions = meta.get("fantasy_positions") or [meta.get("position")]
    return next((p for p in positions if p in config.SCORING_TYPES), None)


def candidate(state: WeekState, player_id: str, *, now_ms: int | None = None) -> lineup.Candidate:
    meta = state.players.get(player_id)
    if not meta or (pos := _position(meta)) is None:
        raise StateError(f"unknown player or position for {player_id}")
    reason = None
    if player_id in state.reserve:
        reason = "on injured reserve; activation required"
    elif not meta.get("team"):
        reason = "no current NFL team"
    elif meta["team"] in state.unavailable_teams:
        reason = "game postponed/canceled; verify rescheduled kickoff"
    index = state.my_starters.index(player_id) if player_id in state.my_starters else None
    return lineup.Candidate(
        player_id=player_id,
        name=meta.get("full_name") or (
            f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()
        ) or player_id,
        pos=pos, points=state.projections.get(player_id),
        status=meta.get("injury_status"), on_bye=state.on_bye(player_id),
        locked=state.is_locked(player_id, now_ms=now_ms),
        started=index is not None, current_slot=index,
        kickoff_ms=state.kickoff_ms(player_id), unavailable_reason=reason,
        positions=tuple(meta.get("fantasy_positions") or (pos,)),
    )


def my_candidates(state: WeekState, *, now_ms: int | None = None) -> list[lineup.Candidate]:
    return [candidate(state, p, now_ms=now_ms) for p in state.my_player_ids]


def free_agents(
    state: WeekState, position: str, *, limit: int = 10, now_ms: int | None = None,
) -> list[lineup.Candidate]:
    """Unowned candidates. Claimability must be confirmed in Sleeper."""
    if position not in config.SCORING_TYPES or limit < 1:
        raise ValueError("invalid free-agent position or limit")
    out = []
    for pid, meta in state.players.items():
        if not state.is_free(pid) or _position(meta) != position:
            continue
        c = candidate(state, pid, now_ms=now_ms)
        if not lineup.ineligibility(c) and not c.locked:
            out.append(c)
    return sorted(out, key=lambda c: (-(c.points or 0.0), c.player_id))[:limit]


def acquisition_note(state: WeekState, player_id: str) -> str:
    drops = [
        t for t in state.transactions
        if t.get("status") == "complete" and player_id in (t.get("drops") or {})
    ]
    if drops:
        return "recently dropped; check waiver countdown and the 24-hour hold rule in Sleeper"
    return "unowned; confirm Free Agent vs Waiver and any priority cost in Sleeper"


@dataclasses.dataclass(slots=True)
class LockWave:
    kickoff_ms: int
    teams: list[str]
    players: list[lineup.Candidate]
    _now: int = 0

    @property
    def passed(self) -> bool:
        return self._now >= self.kickoff_ms


def lock_waves(state: WeekState, *, now_ms: int | None = None) -> list[LockWave]:
    now = state.fetched_at_ms if now_ms is None else now_ms
    by_time: dict[int, list[str]] = {}
    for team, kickoff in state.kickoffs.items():
        by_time.setdefault(kickoff, []).append(team)
    mine = my_candidates(state, now_ms=now)
    return [
        LockWave(start, sorted(teams), affected, now)
        for start, teams in sorted(by_time.items())
        if (affected := [c for c in mine if state.team_of(c.player_id) in teams])
    ]


def next_lock(state: WeekState, *, now_ms: int | None = None) -> LockWave | None:
    return next((w for w in lock_waves(state, now_ms=now_ms) if not w.passed), None)


@dataclasses.dataclass(slots=True)
class Stream:
    position: str
    add: lineup.Candidate
    drop: lineup.Candidate | None
    gain: float
    acquisition: str = "confirm claimability in Sleeper"
    warning: str = "one-week projection only; dropping a player loses future value"

    @property
    def worth_doing(self) -> bool:
        return self.gain > 0


def stream_recommendations(
    state: WeekState, cfg: config.LeagueConfig, *,
    positions: Sequence[str] = STREAM_POSITIONS, now_ms: int | None = None,
) -> list[Stream]:
    if any(pos not in STREAM_POSITIONS for pos in positions):
        raise ValueError("streaming comparisons currently support dedicated DEF/K positions only")
    if state.move_warnings:
        return []  # The caller displays the blocking roster warnings.
    mine = my_candidates(state, now_ms=now_ms)
    base = lineup.optimise_for(mine, cfg)
    active = [c for c in mine if c.player_id not in state.reserve]
    room = len(active) < cfg.roster_size
    out = []
    for pos in positions:
        # Do not suggest a replacement after this position's starter has locked.
        if any(s.pinned and s.name == pos for s in base.slots):
            continue
        held = [c for c in active if c.pos == pos]
        # Unknown production is not zero. The briefing surfaces missing forecasts;
        # no gain for this position is identifiable until they are available.
        if any(not c.locked and lineup.ineligibility(c) == "missing projection" for c in held):
            continue
        if room:
            drops: list[lineup.Candidate | None] = [None]
        elif held:
            drops = [c for c in held if not c.locked]
        else:
            # No automatic sacrificing of a skill-position bench asset.
            continue
        options = []
        for add in free_agents(state, pos, limit=5, now_ms=now_ms):
            for drop in drops:
                roster = [c for c in mine if drop is None or c.player_id != drop.player_id] + [add]
                proposed = lineup.optimise_for(roster, cfg)
                if len(proposed.unfilled) > len(base.unfilled):
                    continue
                if add.player_id not in {c.player_id for c in proposed.starters()}:
                    continue
                options.append(Stream(
                    pos, add, drop, proposed.total - base.total,
                    acquisition_note(state, add.player_id),
                ))
        if options:
            out.append(max(options, key=lambda s: (s.gain, s.add.player_id)))
    return sorted(out, key=lambda s: -s.gain)


@dataclasses.dataclass(slots=True)
class Change:
    action: str
    player: lineup.Candidate
    slot: str
    slot_index: int = -1


def lineup_changes(state: WeekState, optimal: lineup.Lineup) -> list[Change]:
    current = set(state.my_starters) - {"0"}
    proposed = {c.player_id for c in optimal.starters()}
    changes = []
    for pid in sorted(current - proposed):
        c = candidate(state, pid)
        if c.locked:
            raise StateError("attempted to bench a locked starter")
        changes.append(Change("BENCH", c, "BN"))
    for index, slot in enumerate(optimal.slots):
        if not slot.player:
            continue
        before = state.my_starters[index] if index < len(state.my_starters) else "0"
        if slot.player.player_id != before:
            changes.append(Change(
                "MOVE" if slot.player.player_id in current else "START",
                slot.player, slot.name, index,
            ))
    return changes
