"""Durable, bounded jobs for a single-host SQLite deployment.

Operational job state is mutable and deliberately separate from immutable
evidence. A host-local file lock elects one worker even with multiple API
processes sharing this directory. This is not a distributed queue.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import pathlib
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .archive import encode, now_ms

LOG = logging.getLogger(__name__)


class QueueFull(ValueError):
    pass


class IdempotencyConflict(ValueError):
    pass


class JobStore:
    def __init__(self, path: pathlib.Path, *, capacity: int = 10):
        self.path = pathlib.Path(path)
        self.capacity = capacity
        if capacity < 1:
            raise ValueError("job capacity must be positive")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"unsupported job schema {version}")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('collect','evaluate')),
                    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
                    payload TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL, started_at_ms INTEGER,
                    finished_at_ms INTEGER, snapshot_id TEXT,
                    result TEXT, error TEXT, idempotency_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,created_at_ms);
                PRAGMA user_version=1;
            """)

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row) -> dict:
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def get(self, job_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self.decode(row) if row else None

    def enqueue(self, kind: str, payload: dict, key: str | None = None) -> dict:
        if kind not in ("collect", "evaluate"):
            raise ValueError("unsupported job kind")
        encoded = encode(payload).decode()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                existing = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
                if existing:
                    if existing["kind"] != kind or existing["payload"] != encoded:
                        raise IdempotencyConflict("idempotency key was already used for a different request")
                    return self.decode(existing)
            count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= self.capacity:
                raise QueueFull("job queue is full; wait for current work to finish")
            job_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO jobs(id,kind,status,payload,created_at_ms,snapshot_id,idempotency_key)
                   VALUES (?,?,'queued',?,?,?,?)""",
                (job_id, kind, encoded, now_ms(), payload.get("snapshot_id"), key),
            )
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self.decode(row)

    def claim(self) -> dict | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at_ms,rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',started_at_ms=? WHERE id=?", (now_ms(), row["id"]))
            row = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        return self.decode(row)

    def attach_snapshot(self, job_id: str, snapshot_id: str):
        with self.connection() as db:
            db.execute("UPDATE jobs SET snapshot_id=? WHERE id=? AND status='running'", (snapshot_id, job_id))

    def finish(self, job_id: str, *, result: dict | None = None, error: str | None = None, snapshot_id=None):
        if (result is None) == (error is None):
            raise ValueError("job completion must have exactly one result or error")
        with self.connection() as db:
            updated = db.execute(
                """UPDATE jobs SET status=?,result=?,error=?,finished_at_ms=?,
                   snapshot_id=COALESCE(?,snapshot_id) WHERE id=? AND status='running'""",
                ("failed" if error is not None else "succeeded",
                 encode(result).decode() if result is not None else None,
                 error, now_ms(), snapshot_id, job_id),
            )
            if updated.rowcount != 1:
                raise ValueError("only a running job can be completed")

    def recover_interrupted(self):
        """Call only while holding the exclusive worker lock."""
        with self.connection() as db:
            db.execute(
                """UPDATE jobs SET status='failed',finished_at_ms=?,error=?
                   WHERE status='running'""",
                (now_ms(), "Worker was interrupted. Inspect snapshot history before retrying."),
            )

    def list(self, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("job list limit must be in 1..100")
        with self.connection() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at_ms DESC,rowid DESC LIMIT ?", (limit,)).fetchall()
        return [self.decode(row) for row in rows]

    def stats(self) -> dict:
        with self.connection() as db:
            rows = db.execute("SELECT status,count(*) AS n FROM jobs GROUP BY status").fetchall()
        counts = {key: 0 for key in ("queued", "running", "succeeded", "failed")}
        counts.update({row["status"]: row["n"] for row in rows})
        return counts


class JobRunner:
    def __init__(self, store: JobStore, handler: Callable[[dict], dict]):
        self.store, self.handler = store, handler
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self.leading = False

    def start(self):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("job runner is already started")
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._supervise, daemon=True, name="ffopt-job-runner")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                LOG.warning("An active job is still finishing; forced shutdown will mark it interrupted on restart.")

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and not self.stop_event.is_set())

    def _supervise(self):
        lock_path = self.store.path.with_suffix(".worker.lock")
        with lock_path.open("a") as lock:
            while not self.stop_event.is_set():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self.stop_event.wait(0.5)
                    continue
                self.leading = True
                try:
                    self.store.recover_interrupted()
                    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ffopt-operation") as pool:
                        while not self.stop_event.is_set():
                            job = self.store.claim()
                            if job is None:
                                self.wake.wait(0.5)
                                self.wake.clear()
                                continue
                            future = pool.submit(self.handler, job)
                            error = future.exception()
                            if error is not None:
                                LOG.error("Job %s failed", job["id"],
                                          exc_info=(type(error), error, error.__traceback__))
                                self.store.finish(
                                    job["id"], error=str(error)[:4000] or type(error).__name__,
                                    snapshot_id=getattr(error, "snapshot_id", None),
                                )
                            else:
                                self.store.finish(job["id"], result=future.result())
                finally:
                    self.leading = False
                    fcntl.flock(lock, fcntl.LOCK_UN)
                return
