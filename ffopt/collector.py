"""Scheduled one-shot collection and network-free replay of baseline inputs."""

from __future__ import annotations

import gzip
import json
from typing import Callable

from . import archive, client, config, inseason, policies

BASE_ROLES = (
    "nfl_state", "league", "rosters", "matchups", "projections", "players",
    "scores", "transactions", "nfl_state_end",
)


def engine_fingerprint() -> str:
    names = ("archive", "collector", "config", "inseason", "lineup", "policies", "scoring")
    hashes = {
        name: archive.digest((config.REPO_ROOT / "ffopt" / f"{name}.py").read_bytes())
        for name in names
    }
    return archive.digest(archive.encode(hashes))


def required_roles(week: int) -> list[str]:
    return list(BASE_ROLES) + (["previous_transactions"] if week > 1 else [])


def _reject_constant(value):
    raise ValueError(f"non-finite JSON constant {value}")


def decode(body: bytes, headers: dict) -> object:
    encoding = headers.get("content-encoding", "identity").lower()
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding not in ("", "identity"):
        raise ValueError(f"unsupported HTTP content encoding {encoding}")
    return json.loads(body, parse_constant=_reject_constant)


class CaptureError(archive.ArchiveError):
    def __init__(self, snapshot_id: str, errors: list[str]):
        self.snapshot_id = snapshot_id
        super().__init__(f"capture {snapshot_id} is not usable: {'; '.join(errors)}")


def collect(
    store: archive.Archive, cfg: config.LeagueConfig, *,
    week: int | None = None, refresh: bool = False,
    fetch: Callable[..., client.HttpDocument] | None = None,
) -> str:
    """Capture current inputs, preserving errors and never falling back to expired data.

    ``refresh`` bypasses archive reuse, including the daily player map. The
    ordinary client's mutable disk cache is deliberately not an evidence source.
    """
    if week is not None and (isinstance(week, bool) or not isinstance(week, int) or not 1 <= week <= 18):
        raise ValueError("week must be in 1..18")
    request = fetch or client.fetch_document
    previous_captures = store.list(1, league_id=cfg.league_id)
    # A formerly valid cached reference can conflict with newer rosters (e.g.
    # a newly added player). Do not repeat that inconsistency until its TTL ends.
    if previous_captures and previous_captures[0]["status"] == "invalid":
        refresh = True
    snapshot_id = store.begin(cfg, week, engine_fingerprint())
    data, observations, errors = {}, {}, []

    def source(role: str, url: str, ttl: int, *, force: bool = False, timeout: int = 30):
        start = archive.now_ms()
        previous = None if refresh or force else store.recent(url, at_ms=start, max_age_seconds=ttl)
        if previous is not None:
            document = client.HttpDocument(
                previous["request_url"], previous["requested_at_ms"], previous["received_at_ms"],
                previous["status_code"], previous["headers"], store.body(previous["body_hash"]),
            )
        else:
            try:
                # Large player references retain normal CDN behavior; changing
                # league/game state uses the same cache-busting convention as live picks.
                request_url = url if role == "players" else client._uncached(url)
                document = request(request_url, timeout=timeout)
            except (client.ApiError, OSError) as exc:
                observations[role] = store.failure(
                    snapshot_id, role, url, requested_at_ms=start,
                    error=str(exc), max_age_seconds=ttl,
                )
                errors.append(f"{role}: {exc}")
                return None
        error = None
        value = None
        if not 200 <= document.status < 300:
            error = f"HTTP {document.status}"
        else:
            try:
                value = decode(document.body, document.headers)
            except (ValueError, OSError, EOFError) as exc:
                error = f"invalid JSON response: {exc}"
        observations[role] = store.observe(
            snapshot_id, role, url, document=document, max_age_seconds=ttl,
            error=error, reused_from=previous["id"] if previous is not None else None,
        )
        if error is not None:
            errors.append(f"{role}: {error}")
        else:
            data[role] = value
        return value

    # The large daily reference can take most of its 120-second timeout.
    # Fetch it before starting the freshness window for small live sources.
    source("players", f"{client.API_V1}/players/nfl", 86400, timeout=120)
    nfl = source("nfl_state", f"{client.API_V1}/state/nfl", 30)
    resolved = nfl.get("week") if isinstance(nfl, dict) else None
    season = str(nfl["season"]) if isinstance(nfl, dict) and nfl.get("season") else None
    if (
        isinstance(resolved, bool) or not isinstance(resolved, int) or not 1 <= resolved <= 18
        or season != cfg.season or (week is not None and week != resolved)
    ):
        errors.append("requested/configured season and week must match the captured current NFL state")
        store.finish(
            snapshot_id, season=season, week=week, status="incomplete",
            required_roles=list(BASE_ROLES), errors=errors, finished_at_ms=archive.now_ms(),
        )
        raise CaptureError(snapshot_id, errors)
    week = resolved
    base = f"{client.API_V1}/league/{cfg.league_id}"
    source("league", base, 120)
    source("rosters", base + "/rosters", 60)
    source("matchups", f"{base}/matchups/{week}", 30)
    source(
        "projections",
        client._ordered(
            f"{client.API_V2}/projections/nfl/{season}/{week}"
            f"?season_type=regular&{client.POSITION_QUERY}", "pts_ppr",
        ),
        300,
    )
    source("scores", f"{client.API_V2}/scores/nfl/regular/{season}/{week}", 30)
    source("transactions", f"{base}/transactions/{week}", 60)
    if week > 1:
        source("previous_transactions", f"{base}/transactions/{week - 1}", 600)
    end = source("nfl_state_end", f"{client.API_V1}/state/nfl", 30, force=True)
    if not isinstance(end, dict) or (end.get("season"), end.get("week")) != (nfl.get("season"), week):
        errors.append("NFL season/week changed or became unavailable during capture")
    finished = archive.now_ms()
    status = "incomplete" if errors else "complete"
    if not errors:
        try:
            validate_observations(observations, finished)
            state = inseason.build_state(
                cfg, week, data, observed_at_ms=finished,
                sources={role: o["received_at_ms"] for role, o in observations.items()},
                snapshot_id=snapshot_id,
            )
            archive.encode(policies.ExpectedPoints().recommend(cfg, state))
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"snapshot validation: {exc}")
            status = "invalid"
    store.finish(
        snapshot_id, season=season, week=week, status=status,
        required_roles=required_roles(week), errors=errors, finished_at_ms=finished,
    )
    if errors:
        raise CaptureError(snapshot_id, errors)
    return snapshot_id


def validate_observations(observations: dict[str, dict], at_ms: int):
    for role, obs in observations.items():
        if obs["error"] or obs["body_hash"] is None or not 200 <= obs["status_code"] < 300:
            raise archive.ArchiveError(f"failed source: {role}")
        if not obs["requested_at_ms"] <= obs["received_at_ms"] <= obs["recorded_at_ms"] <= at_ms:
            raise archive.ArchiveError(f"invalid/future timestamps for {role}")
        if at_ms - obs["received_at_ms"] > obs["max_age_seconds"] * 1000:
            raise archive.ArchiveError(f"{role} expired during capture; collect again")


def replay(store: archive.Archive, snapshot_id: str) -> tuple[config.LeagueConfig, inseason.WeekState]:
    """Replay at the archived epoch, with the archived configuration. No network."""
    manifest = store.manifest(snapshot_id)
    if manifest["status"] != "complete":
        raise CaptureError(snapshot_id, manifest["errors"] or ["unfinished capture"])
    week = manifest["week"]
    observations = {o["role"]: o for o in manifest["observations"]}
    missing = set(required_roles(week)) - observations.keys()
    if missing:
        raise archive.ArchiveError(f"missing replay inputs: {sorted(missing)}")
    validate_observations(observations, manifest["finished_at_ms"])
    data = {
        role: decode(store.body(obs["body_hash"]), obs["headers"])
        for role, obs in observations.items()
    }
    if any(
        data["nfl_state"].get(key) != data["nfl_state_end"].get(key)
        for key in ("season", "week")
    ):
        raise archive.ArchiveError("captured NFL-state bookends disagree")
    cfg = config.LeagueConfig(json.loads(store.body(manifest["config_hash"])))
    return cfg, inseason.build_state(
        cfg, week, data, observed_at_ms=manifest["finished_at_ms"],
        sources={role: o["received_at_ms"] for role, o in observations.items()},
        snapshot_id=snapshot_id,
    )
