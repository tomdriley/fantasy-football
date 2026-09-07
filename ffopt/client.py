"""Cached HTTP client for the platform's public JSON API.

All endpoints used here are public and unauthenticated. Responses are cached on
disk because the draft has a 60-second decision deadline: the large reference
payloads must never be re-fetched mid-draft.

Cache policy is per-endpoint:
  * reference data (item pool, forecasts, historical outcomes) -> long TTL
  * live draft picks -> never cached, always fresh
"""

from __future__ import annotations

import json
import dataclasses
import os
import pathlib
import time
import urllib.error
import urllib.request
from typing import Any

from . import config

#: Live endpoints. Overridable so the whole stack can be pointed at a mock
#: draft server and exercised end to end, including the polling path, without
#: any change to application code. See scripts/mock_draft.py.
API_V1 = os.environ.get("FFOPT_API_V1", "https://api.sleeper.app/v1")
API_V2 = os.environ.get("FFOPT_API_V2", "https://api.sleeper.com")

CACHE_DIR = config.REPO_ROOT / "data" / "cache"
DEFAULT_TTL = 12 * 3600
USER_AGENT = "ffopt/0.1 (personal fantasy draft tool)"

POSITION_QUERY = "&".join(f"position[]={p}" for p in config.SCORING_TYPES)


class ApiError(RuntimeError):
    pass


def _cache_path(key: str) -> pathlib.Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    return CACHE_DIR / f"{safe}.json"


@dataclasses.dataclass(frozen=True)
class HttpDocument:
    url: str
    requested_at_ms: int
    received_at_ms: int
    status: int
    headers: dict[str, str]
    body: bytes


PROVENANCE_HEADERS = frozenset({
    "date", "age", "etag", "last-modified", "cache-control", "content-type",
    "content-encoding", "cf-cache-status",
})


def fetch_document(url: str, timeout: float = 30.0) -> HttpDocument:
    """Fetch bytes and a small non-secret header allowlist, without disk caching."""
    started = int(time.time() * 1000)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    def document(resp) -> HttpDocument:
        body = resp.read()
        return HttpDocument(
            url, started, int(time.time() * 1000), resp.status,
            {k.lower(): v for k, v in resp.headers.items() if k.lower() in PROVENANCE_HEADERS},
            body,
        )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return document(resp)
    except urllib.error.HTTPError as e:
        with e:
            return document(e)
    except urllib.error.URLError as e:
        raise ApiError(f"network error for {url}: {e.reason}") from e
    except TimeoutError as e:
        raise ApiError(f"timeout for {url}") from e


def _fetch(url: str, timeout: float = 30.0) -> Any:
    response = fetch_document(url, timeout)
    if not 200 <= response.status < 300:
        raise ApiError(f"HTTP {response.status} for {url}")
    try:
        return json.loads(response.body)
    except (ValueError, UnicodeError) as e:
        raise ApiError(f"invalid JSON for {url}") from e


def get(
    url: str, key: str, ttl: float = DEFAULT_TTL, timeout: float = 30.0, *,
    allow_stale: bool = True, refresh: bool = False,
) -> Any:
    """Fetch `url`, caching under `key`. ttl<=0 bypasses the cache entirely.

    On network failure a stale cache entry is preferred over raising, so the
    live tool degrades rather than dies mid-draft.
    """
    path = _cache_path(key)
    if not refresh and ttl > 0 and path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        with open(path) as f:
            return json.load(f)
    try:
        data = _fetch(url, timeout=timeout)
    except ApiError:
        if allow_stale and path.exists():
            with open(path) as f:
                return json.load(f)
        raise
    if ttl > 0:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Temp name is process-unique: parallel backtest workers share this cache
        # directory, and a fixed temp name lets one process rename a file another
        # is still writing, which surfaced as a FileNotFoundError mid-sweep.
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
    return data


# -- endpoints ----------------------------------------------------------


def projections(season: str | int) -> list[dict]:
    """Pre-season forecasts, including per-stat quantities and consensus order."""
    url = (
        f"{API_V2}/projections/nfl/{season}"
        f"?season_type=regular&{POSITION_QUERY}&order_by=adp_std"
    )
    return get(url, f"projections_{season}")


def realized_stats(season: str | int) -> list[dict]:
    """Actual realized outcomes for a completed season (for the backtest)."""
    url = (
        f"{API_V2}/stats/nfl/{season}"
        f"?season_type=regular&{POSITION_QUERY}&order_by=pts_ppr"
    )
    return get(url, f"stats_{season}")


def league(league_id: str) -> dict:
    return get(f"{API_V1}/league/{league_id}", f"league_{league_id}")


def draft(draft_id: str) -> dict:
    """Draft metadata. Briefly cached, but never from a stale CDN copy: the
    draft order is published minutes before the start and we must see it."""
    return get(_uncached(f"{API_V1}/draft/{draft_id}"), f"draft_{draft_id}", ttl=60)


def _uncached(url: str) -> str:
    """Add a unique query parameter so the CDN cannot serve a stored copy.

    Measured on the live endpoint: the picks feed is served through Cloudflare
    with `cache-control: public, s-maxage=30, stale-while-revalidate=300`, and
    a plain request returns `cf-cache-status: HIT` with an `age` of up to 30
    seconds. On a 60-second pick timer that is half a pick of lag in the normal
    case, and `stale-while-revalidate` permits far worse.

    A unique parameter is a distinct cache key, so it misses and reaches
    origin: verified returning `cf-cache-status: MISS` with no `age` header.
    The cost is real origin traffic, but polling every 1.5-5s is 12-40 requests
    a minute against a documented budget of 1000.

    Only used for the live feed. Projections and the player map are large,
    static for the day, and should stay cached.
    """
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_={time.time_ns()}"


def draft_picks(draft_id: str) -> list[dict]:
    """Live claim feed. Never cached, locally or at the CDN."""
    url = _uncached(f"{API_V1}/draft/{draft_id}/picks")
    return get(url, f"picks_{draft_id}", ttl=0, timeout=10)


# -- in-season endpoints ------------------------------------------------
#
# TTLs differ by how fast the underlying fact can change:
#   weekly projections   -> hours (revised as injury news lands)
#   completed weeks      -> effectively immutable
#   rosters/transactions -> minutes (a rival can act at any time)
#   the player map       -> daily, and 14 MB, so never on a decision path

WEEK_TTL = 3 * 3600
FINAL_TTL = 30 * 24 * 3600
LIVE_TTL = 120

#: Valid `order_by` keys on the stats/projections endpoints.
#:
#: MEASURED 2026-09-05: an *invalid* key does not error. `order_by=ppr` returns
#: HTTP 200 with the full payload sorted so that players carrying **no
#: projection at all** come first -- reading results[0] then yields a player
#: with an empty stat block, which is indistinguishable from a broken feed.
#: This cost a false "historical weekly data is unavailable" conclusion during
#: analysis. The sort key is validated here so the mistake cannot recur.
VALID_ORDER_BY = frozenset({"pts_ppr", "pts_half_ppr", "pts_std", "adp_std"})


class InvalidSortKey(ValueError):
    """Raised instead of letting the API silently return a useless ordering."""


def _ordered(url: str, order_by: str | None) -> str:
    if order_by is None:
        return url
    if order_by not in VALID_ORDER_BY:
        raise InvalidSortKey(
            f"{order_by!r} is not a valid order_by key; the API accepts it "
            f"silently and returns unprojected players first. "
            f"Use one of {sorted(VALID_ORDER_BY)}."
        )
    return f"{url}&order_by={order_by}"


def weekly_projections(
    season: str | int, week: int, *, order_by: str | None = "pts_ppr",
    refresh: bool = False,
) -> list[dict]:
    """Per-week forecasts. Revised through the week as injury news lands."""
    url = _ordered(
        f"{API_V2}/projections/nfl/{season}/{week}"
        f"?season_type=regular&{POSITION_QUERY}",
        order_by,
    )
    key = f"wproj_{season}_{week}"
    if order_by != "pts_ppr":
        key += f"_{order_by or 'unsorted'}"
    return get(url, key, ttl=WEEK_TTL, allow_stale=False, refresh=refresh)


def weekly_stats(
    season: str | int, week: int, *, final: bool = False,
    order_by: str | None = "pts_ppr",
) -> list[dict]:
    """Per-week realized outcomes. `final` marks a completed week as immutable."""
    url = _ordered(
        f"{API_V2}/stats/nfl/{season}/{week}?season_type=regular&{POSITION_QUERY}",
        order_by,
    )
    key = f"wstat_{season}_{week}"
    if order_by != "pts_ppr":
        key += f"_{order_by or 'unsorted'}"
    return get(url, key, ttl=FINAL_TTL if final else LIVE_TTL, allow_stale=False)


def scores(season: str | int, week: int, *, refresh: bool = False) -> list[dict]:
    """NFL games for a week.

    The single richest endpoint on the platform, and the one this project had
    not been using: it carries `start_time` (epoch ms) per game, which is what
    determines when each player's roster slot locks, plus the betting spread,
    moneyline and stadium details.
    """
    return get(
        f"{API_V2}/scores/nfl/regular/{season}/{week}",
        f"scores_{season}_{week}",
        ttl=LIVE_TTL, allow_stale=False, refresh=refresh,
    )


def rosters(league_id: str, *, refresh: bool = False) -> list[dict]:
    """All ten rosters, including `reserve` and `waiver_position`."""
    return get(
        _uncached(f"{API_V1}/league/{league_id}/rosters"),
        f"rosters_{league_id}", ttl=LIVE_TTL, allow_stale=False, refresh=refresh,
    )


def matchups(league_id: str, week: int, *, refresh: bool = False) -> list[dict]:
    """Weekly pairings.

    `starters` is populated *before* kickoff, so an opponent's lineup is
    readable while it can still inform our own.
    """
    return get(
        _uncached(f"{API_V1}/league/{league_id}/matchups/{week}"),
        f"matchups_{league_id}_{week}", ttl=LIVE_TTL, allow_stale=False, refresh=refresh,
    )


def transactions(league_id: str, week: int, *, refresh: bool = False) -> list[dict]:
    """Adds, drops, waiver claims and trades for a week."""
    return get(
        _uncached(f"{API_V1}/league/{league_id}/transactions/{week}"),
        f"transactions_{league_id}_{week}", ttl=LIVE_TTL, allow_stale=False, refresh=refresh,
    )


def players(*, refresh: bool = False) -> dict[str, dict]:
    """The full player map: injury status, depth chart, bye, team.

    ~14 MB. Cached for a day and never fetched on a decision path.
    """
    return get(
        f"{API_V1}/players/nfl", "players_nfl", ttl=24 * 3600, timeout=120,
        allow_stale=False, refresh=refresh,
    )


def nfl_state() -> dict:
    """Current season and week, as the platform sees them."""
    return get(f"{API_V1}/state/nfl", "state_nfl", ttl=LIVE_TTL, allow_stale=False)


def trending(kind: str = "add", *, lookback_hours: int = 24, limit: int = 25) -> list[dict]:
    """League-wide add/drop momentum -- a crowd signal, not a projection."""
    if kind not in ("add", "drop"):
        raise ValueError("kind must be 'add' or 'drop'")
    return get(
        f"{API_V1}/players/nfl/trending/{kind}"
        f"?lookback_hours={lookback_hours}&limit={limit}",
        f"trending_{kind}_{lookback_hours}_{limit}", ttl=LIVE_TTL,
    )
