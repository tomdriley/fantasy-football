"""Framework-independent application service and background operations."""

import json
import pathlib
from functools import lru_cache

from . import advice, archive, collector, config
from .evaluation import evaluate_snapshot
from .jobs import JobRunner, JobStore


class AdvisorService:
    def __init__(
        self, archive_path: pathlib.Path, jobs_path: pathlib.Path, rules_path: pathlib.Path,
        *, queue_capacity: int = 10,
    ):
        self.archive = archive.Archive(archive_path)
        self.jobs = JobStore(jobs_path, capacity=queue_capacity)
        self.rules_path = rules_path
        self.runner = JobRunner(self.jobs, self.run_job)

    def config(self):
        return config.read(self.rules_path)

    def run_job(self, job: dict) -> dict:
        payload = job["payload"]
        if job["kind"] == "collect":
            snapshot_id = collector.collect(
                self.archive, self.config(), refresh=payload["refresh"],
            )
            self.jobs.attach_snapshot(job["id"], snapshot_id)
        else:
            snapshot_id = payload["snapshot_id"]
        result = evaluate_snapshot(self.archive, snapshot_id, payload.get("minimum_pickup_gain"))
        return {"snapshot_id": snapshot_id, "evaluation_id": result["evaluation_id"]}

    def snapshot(self, snapshot_id: str) -> dict:
        manifest = self.archive.manifest(snapshot_id)
        raw = json.loads(self.archive.body(manifest["config_hash"]))
        evaluations = self.archive.evaluations(snapshot_id, limit=1)
        return {
            "snapshot": manifest,
            "league": {
                "name": raw["league"]["name"], "season": raw["league"]["season"],
                "team_name": raw.get("my_team", {}).get("team_name") or "My team",
            },
            "latest_evaluation": evaluations[-1] if evaluations else None,
        }

    @lru_cache(maxsize=32)
    def _advice_context(self, snapshot_id: str) -> dict:
        cfg, state = collector.replay(self.archive, snapshot_id)
        return advice.context(cfg, state)

    def advice(self, snapshot_id: str | None = None) -> dict:
        mode = "historical" if snapshot_id else "current"
        if snapshot_id is not None:
            selected = self.archive.manifest(snapshot_id)
            cfg = config.LeagueConfig(json.loads(self.archive.body(selected["config_hash"])))
            latest = selected
        else:
            cfg = self.config()
            attempts = self.archive.list(1, league_id=cfg.league_id)
            latest = self.archive.manifest(attempts[0]["id"]) if attempts else None
        if snapshot_id is None:
            successful = self.archive.list(1, league_id=cfg.league_id, status="complete", with_evaluation=True)
            snapshot_id = successful[0]["id"] if successful else (latest["id"] if latest else None)
        if latest:
            latest["analysis_ready"] = bool(self.archive.evaluations(latest["id"], limit=1))
        manifest = evaluation = roster_context = None
        league = {
            "name": cfg.raw["league"]["name"], "season": cfg.season,
            "team_name": cfg.raw["my_team"]["team_name"],
        }
        if snapshot_id:
            detail = self.snapshot(snapshot_id)
            manifest, evaluation, league = detail["snapshot"], detail["latest_evaluation"], detail["league"]
            if manifest["status"] == "complete" and evaluation:
                roster_context = self._advice_context(snapshot_id)
            if mode == "historical":
                latest = manifest
        return advice.build(
            mode=mode, now_ms=archive.now_ms(), league=league, manifest=manifest,
            latest_attempt=latest, evaluation=evaluation, roster_context=roster_context,
            current_cfg=cfg,
        )
