"""Append-only local evidence store, independent of collection and policies.

Raw HTTP bodies are compressed and addressed by SHA-256. Reusing an observation
does not change its original request/receipt timestamps. SQLite is intended for
a single host with durable local storage, not an ephemeral container filesystem.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import pathlib
import sqlite3
import time
import uuid

from . import client, config

DEFAULT_PATH = config.REPO_ROOT / "data" / "archive.sqlite3"
SCHEMA_VERSION = 1


class ArchiveError(ValueError):
    pass


class SnapshotNotFound(ArchiveError):
    pass


def encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class Archive:
    def __init__(self, path: pathlib.Path = DEFAULT_PATH, *, create: bool = True):
        self.path = pathlib.Path(path)
        if not create and not self.path.is_file():
            raise FileNotFoundError(f"snapshot archive not found: {self.path}")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and create:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS blobs (
                        hash TEXT PRIMARY KEY, raw_bytes INTEGER NOT NULL,
                        gzip_body BLOB NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS captures (
                        id TEXT PRIMARY KEY, started_at_ms INTEGER NOT NULL,
                        league_id TEXT NOT NULL, requested_week INTEGER,
                        config_hash TEXT NOT NULL REFERENCES blobs(hash),
                        engine_fingerprint TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS observations (
                        id INTEGER PRIMARY KEY,
                        capture_id TEXT NOT NULL REFERENCES captures(id),
                        role TEXT NOT NULL, canonical_url TEXT NOT NULL,
                        request_url TEXT NOT NULL,
                        requested_at_ms INTEGER NOT NULL,
                        received_at_ms INTEGER NOT NULL,
                        recorded_at_ms INTEGER NOT NULL,
                        status_code INTEGER, headers TEXT NOT NULL,
                        body_hash TEXT REFERENCES blobs(hash),
                        origin TEXT NOT NULL CHECK(origin IN ('network','archive_reuse')),
                        reused_from INTEGER REFERENCES observations(id),
                        max_age_seconds INTEGER NOT NULL,
                        error TEXT,
                        UNIQUE(capture_id, role)
                    );
                    CREATE INDEX IF NOT EXISTS observation_cache ON observations(canonical_url, received_at_ms);
                    CREATE TABLE IF NOT EXISTS capture_results (
                        capture_id TEXT PRIMARY KEY REFERENCES captures(id),
                        finished_at_ms INTEGER NOT NULL, season TEXT,
                        week INTEGER,
                        status TEXT NOT NULL CHECK(status IN ('complete','incomplete','invalid')),
                        required_roles TEXT NOT NULL, errors TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS evaluations (
                        id TEXT PRIMARY KEY,
                        capture_id TEXT NOT NULL REFERENCES captures(id),
                        recorded_at_ms INTEGER NOT NULL,
                        engine_fingerprint TEXT NOT NULL,
                        report TEXT NOT NULL
                    );
                """)
                for table in ("blobs", "captures", "observations", "capture_results", "evaluations"):
                    for action in ("UPDATE", "DELETE"):
                        db.execute(
                            f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} BEFORE {action} ON {table} "
                            "BEGIN SELECT RAISE(ABORT, 'archive evidence is append-only'); END"
                        )
                db.executescript("""
                    CREATE TRIGGER IF NOT EXISTS sealed_capture BEFORE INSERT ON observations
                    WHEN EXISTS (SELECT 1 FROM capture_results WHERE capture_id=NEW.capture_id)
                    BEGIN SELECT RAISE(ABORT, 'capture is already sealed'); END;
                    PRAGMA user_version = 1;
                """)
            elif version != SCHEMA_VERSION:
                raise ArchiveError(f"unsupported archive schema {version}")
            db.execute("CREATE INDEX IF NOT EXISTS evaluations_capture ON evaluations(capture_id,recorded_at_ms)")
            db.execute("CREATE INDEX IF NOT EXISTS captures_league_time ON captures(league_id,started_at_ms)")

    @contextlib.contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _blob(db, body: bytes) -> str:
        key = digest(body)
        db.execute(
            "INSERT OR IGNORE INTO blobs VALUES (?,?,?)",
            (key, len(body), gzip.compress(body, mtime=0)),
        )
        return key

    def body(self, key: str) -> bytes:
        with self._connection() as db:
            row = db.execute("SELECT * FROM blobs WHERE hash=?", (key,)).fetchone()
        if row is None:
            raise ArchiveError(f"missing archived payload {key}")
        try:
            raw = gzip.decompress(row["gzip_body"])
        except (OSError, EOFError) as exc:
            raise ArchiveError(f"corrupt archived payload {key}") from exc
        if len(raw) != row["raw_bytes"] or digest(raw) != key:
            raise ArchiveError(f"archived payload hash mismatch: {key}")
        return raw

    def begin(self, cfg: config.LeagueConfig, week: int | None, fingerprint: str) -> str:
        capture_id = str(uuid.uuid4())
        with self._connection() as db:
            cfg_hash = self._blob(db, encode(cfg.raw))
            db.execute(
                "INSERT INTO captures VALUES (?,?,?,?,?,?)",
                (capture_id, now_ms(), cfg.league_id, week, cfg_hash, fingerprint),
            )
        return capture_id

    def observe(
        self, capture_id: str, role: str, canonical_url: str, *,
        document: client.HttpDocument, max_age_seconds: int,
        error: str | None = None, reused_from: int | None = None,
    ) -> dict:
        with self._connection() as db:
            key = self._blob(db, document.body)
            cursor = db.execute(
                """INSERT INTO observations (
                    capture_id,role,canonical_url,request_url,requested_at_ms,
                    received_at_ms,recorded_at_ms,status_code,headers,body_hash,
                    origin,reused_from,max_age_seconds,error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (capture_id, role, canonical_url, document.url, document.requested_at_ms,
                 document.received_at_ms, now_ms(), document.status,
                 encode(document.headers).decode(), key,
                 "archive_reuse" if reused_from is not None else "network",
                 reused_from, max_age_seconds, error),
            )
            row = db.execute("SELECT * FROM observations WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._observation(row)

    def failure(
        self, capture_id: str, role: str, url: str, *,
        requested_at_ms: int, error: str, max_age_seconds: int,
    ) -> dict:
        with self._connection() as db:
            cursor = db.execute(
                """INSERT INTO observations (
                    capture_id,role,canonical_url,request_url,requested_at_ms,
                    received_at_ms,recorded_at_ms,status_code,headers,body_hash,
                    origin,reused_from,max_age_seconds,error
                ) VALUES (?,?,?,?,?,?,?,NULL,'{}',NULL,'network',NULL,?,?)""",
                (capture_id, role, url, url, requested_at_ms, now_ms(), now_ms(),
                 max_age_seconds, error),
            )
            row = db.execute("SELECT * FROM observations WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._observation(row)

    @staticmethod
    def _observation(row) -> dict:
        data = dict(row)
        data["headers"] = json.loads(data["headers"])
        return data

    def recent(self, url: str, *, at_ms: int, max_age_seconds: int) -> dict | None:
        with self._connection() as db:
            row = db.execute(
                """SELECT o.* FROM observations o
                   JOIN capture_results r ON r.capture_id=o.capture_id
                   WHERE r.status='complete' AND o.canonical_url=? AND o.error IS NULL
                     AND o.status_code BETWEEN 200 AND 299
                     AND o.received_at_ms <= ? AND o.received_at_ms > ?
                   ORDER BY o.received_at_ms DESC, o.id DESC LIMIT 1""",
                (url, at_ms, at_ms - max_age_seconds * 1000),
            ).fetchone()
        return self._observation(row) if row is not None else None

    def finish(
        self, capture_id: str, *, season: str | None, week: int | None,
        status: str, required_roles: list[str], errors: list[str], finished_at_ms: int,
    ):
        if status == "complete":
            observations = {o["role"]: o for o in self.manifest(capture_id)["observations"]}
            if errors or any(role not in observations or observations[role]["error"] for role in required_roles):
                raise ArchiveError("cannot mark a missing/failed capture complete")
        with self._connection() as db:
            db.execute(
                "INSERT INTO capture_results VALUES (?,?,?,?,?,?,?)",
                (capture_id, finished_at_ms, season, week, status,
                 encode(required_roles).decode(), encode(errors).decode()),
            )

    def manifest(self, capture_id: str) -> dict:
        with self._connection() as db:
            row = db.execute(
                """SELECT c.*,r.finished_at_ms,r.season,r.week,r.status,r.required_roles,r.errors
                   FROM captures c LEFT JOIN capture_results r ON r.capture_id=c.id WHERE c.id=?""",
                (capture_id,),
            ).fetchone()
            if row is None:
                raise SnapshotNotFound(f"snapshot not found: {capture_id}")
            observations = db.execute(
                "SELECT * FROM observations WHERE capture_id=? ORDER BY id", (capture_id,),
            ).fetchall()
        result = dict(row)
        result["status"] = result["status"] or "unfinished"
        result["required_roles"] = json.loads(result["required_roles"] or "[]")
        result["errors"] = json.loads(result["errors"] or "[]")
        result["observations"] = [self._observation(o) for o in observations]
        result["schema_version"] = SCHEMA_VERSION
        return result

    def list(
        self, limit: int = 20, *, league_id: str | None = None,
        status: str | None = None, with_evaluation: bool = False,
    ) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be in 1..1000")
        if status not in (None, "complete", "invalid", "incomplete", "unfinished"):
            raise ValueError("invalid snapshot status")
        with self._connection() as db:
            rows = db.execute(
                """SELECT c.id,c.league_id,c.started_at_ms,r.finished_at_ms,r.week,r.season,
                          COALESCE(r.status,'unfinished') AS status
                   FROM captures c LEFT JOIN capture_results r ON r.capture_id=c.id
                   WHERE (? IS NULL OR c.league_id=?)
                     AND (? IS NULL OR COALESCE(r.status,'unfinished')=?)
                     AND (?=0 OR EXISTS (SELECT 1 FROM evaluations e WHERE e.capture_id=c.id))
                   ORDER BY c.started_at_ms DESC,c.rowid DESC LIMIT ?""",
                (league_id, league_id, status, status, int(with_evaluation), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def evaluate(self, capture_id: str, report: dict, fingerprint: str) -> str:
        if self.manifest(capture_id)["status"] != "complete":
            raise ArchiveError("cannot evaluate an incomplete snapshot")
        run_id = str(uuid.uuid4())
        with self._connection() as db:
            db.execute(
                "INSERT INTO evaluations VALUES (?,?,?,?,?)",
                (run_id, capture_id, now_ms(), fingerprint, encode(report).decode()),
            )
        return run_id

    def evaluations(self, capture_id: str, *, limit: int | None = None) -> list[dict]:
        if limit is not None and not 1 <= limit <= 1000:
            raise ValueError("evaluation limit must be in 1..1000")
        with self._connection() as db:
            if limit is None:
                rows = db.execute(
                    "SELECT * FROM evaluations WHERE capture_id=? ORDER BY recorded_at_ms,rowid",
                    (capture_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM evaluations WHERE capture_id=? ORDER BY recorded_at_ms DESC,rowid DESC LIMIT ?",
                    (capture_id, limit),
                ).fetchall()[::-1]
        return [{**dict(r), "report": json.loads(r["report"])} for r in rows]

    def backup(self, destination: pathlib.Path):
        destination = pathlib.Path(destination)
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite backup {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as source, contextlib.closing(sqlite3.connect(destination)) as target:
            source.backup(target)

    def stats(self) -> dict:
        with self._connection() as db:
            row = db.execute(
                """SELECT COUNT(*) AS unique_payloads, COALESCE(SUM(raw_bytes),0) AS raw_bytes,
                          COALESCE(SUM(length(gzip_body)),0) AS compressed_bytes FROM blobs"""
            ).fetchone()
        return dict(row)
