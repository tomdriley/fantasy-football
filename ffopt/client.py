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


def _fetch(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise ApiError(f"network error for {url}: {e.reason}") from e


def get(url: str, key: str, ttl: float = DEFAULT_TTL, timeout: float = 30.0) -> Any:
    """Fetch `url`, caching under `key`. ttl<=0 bypasses the cache entirely.

    On network failure a stale cache entry is preferred over raising, so the
    live tool degrades rather than dies mid-draft.
    """
    path = _cache_path(key)
    if ttl > 0 and path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        with open(path) as f:
            return json.load(f)
    try:
        data = _fetch(url, timeout=timeout)
    except ApiError:
        if path.exists():
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
