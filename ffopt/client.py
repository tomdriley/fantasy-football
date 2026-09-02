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
import pathlib
import time
import urllib.error
import urllib.request
from typing import Any

from . import config

API_V1 = "https://api.sleeper.app/v1"
API_V2 = "https://api.sleeper.com"

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
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.replace(path)
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
    return get(f"{API_V1}/draft/{draft_id}", f"draft_{draft_id}", ttl=60)


def draft_picks(draft_id: str) -> list[dict]:
    """Live claim feed. Never cached."""
    return get(f"{API_V1}/draft/{draft_id}/picks", f"picks_{draft_id}", ttl=0, timeout=10)
