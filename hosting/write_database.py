"""One immutable synthetic marker per trusted identity; no runtime provisioning."""

from datetime import datetime, timezone
import hashlib
import os

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout, TooManyRequests

from .auth import Identity
from .database import DatabaseSettings, DatabaseUnavailable

MARKER = "synthetic-write-v1"
WRITER_ROLE = "ffopt_stage_probe_writer"
POLICY_EXPRESSION = "(owner_key = current_setting('ffopt.actor'::text, true))"
ROLE_CHECK = """
SELECT (
    current_user = %s AND current_user = session_user AND r.rolcanlogin
    AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid)
    AND has_database_privilege(current_user, current_database(), 'CONNECT')
    AND NOT has_database_privilege(current_user, current_database(), 'CREATE,TEMP')
    AND NOT has_database_privilege(current_user, current_database(), 'CONNECT WITH GRANT OPTION')
    AND has_schema_privilege(current_user, 'hosting_write_probe', 'USAGE')
    AND NOT has_schema_privilege(current_user, 'hosting_write_probe', 'USAGE WITH GRANT OPTION')
    AND NOT EXISTS (
        SELECT 1 FROM pg_namespace n
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
        AND (n.nspowner = r.oid OR has_schema_privilege(current_user, n.oid, 'CREATE'))
    )
    AND has_table_privilege(current_user, t.oid, 'SELECT')
    AND has_table_privilege(current_user, t.oid, 'INSERT')
    AND NOT has_table_privilege(current_user, t.oid, 'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
    AND NOT has_table_privilege(current_user, t.oid, 'SELECT WITH GRANT OPTION,INSERT WITH GRANT OPTION')
    AND NOT has_any_column_privilege(current_user, t.oid, 'UPDATE,REFERENCES')
    AND NOT has_any_column_privilege(current_user, t.oid, 'SELECT WITH GRANT OPTION,INSERT WITH GRANT OPTION')
    AND NOT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.oid <> t.oid
        AND (
            c.relowner = r.oid
            OR CASE WHEN c.relkind IN ('r','p','v','m','f') THEN (
                has_table_privilege(current_user, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
                OR has_any_column_privilege(current_user, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES')
            ) ELSE false END
            OR CASE WHEN c.relkind = 'S' THEN has_sequence_privilege(current_user, c.oid, 'USAGE,SELECT,UPDATE')
                ELSE false END
        )
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_proc f JOIN pg_namespace n ON n.oid = f.pronamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
        AND (f.proowner = r.oid OR has_function_privilege(current_user, f.oid, 'EXECUTE'))
    )
    AND t.relkind = 'r' AND t.relowner <> r.oid AND t.relrowsecurity AND t.relforcerowsecurity
    AND NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = t.oid)
    AND NOT EXISTS (SELECT 1 FROM pg_rewrite WHERE ev_class = t.oid)
    AND (SELECT count(*) = 3 FROM pg_attribute WHERE attrelid = t.oid AND attnum > 0 AND NOT attisdropped)
    AND EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = t.oid AND a.attname = 'owner_key'
        AND a.atttypid = 'text'::regtype AND a.attnotnull AND NOT a.atthasdef
    )
    AND EXISTS (
        SELECT 1 FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = t.oid AND a.attname = 'value' AND a.attnotnull
        AND a.atttypid = 'text'::regtype AND pg_get_expr(d.adbin, d.adrelid) = '''synthetic-write-v1''::text'
    )
    AND EXISTS (
        SELECT 1 FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = t.oid AND a.attname = 'created_at' AND a.attnotnull
        AND a.atttypid = 'timestamptz'::regtype AND pg_get_expr(d.adbin, d.adrelid) = 'CURRENT_TIMESTAMP'
    )
    AND (SELECT count(*) = 1 FROM pg_policy WHERE polrelid = t.oid)
    AND EXISTS (
        SELECT 1 FROM pg_policy p WHERE p.polrelid = t.oid
        AND p.polname = 'marker_owner' AND p.polcmd = '*' AND p.polpermissive
        AND p.polroles = ARRAY[0::oid]
        AND pg_get_expr(p.polqual, p.polrelid) = %s
        AND pg_get_expr(p.polwithcheck, p.polrelid) = %s
    )
    AND EXISTS (
        SELECT 1 FROM pg_constraint c JOIN pg_attribute a
        ON a.attrelid = c.conrelid AND a.attname = 'owner_key'
        WHERE c.conrelid = t.oid AND c.contype = 'p' AND c.conkey = ARRAY[a.attnum]
    )
) AS allowed
FROM pg_roles r
JOIN pg_class t ON t.oid = 'hosting_write_probe.marker'::regclass
WHERE r.rolname = current_user
"""
SELECT_MARKER = """
SELECT owner_key, value, created_at FROM hosting_write_probe.marker
WHERE owner_key = %s LIMIT 2
"""
INSERT_MARKER = """
INSERT INTO hosting_write_probe.marker (owner_key) VALUES (%s)
ON CONFLICT (owner_key) DO NOTHING
"""


def owner_key(identity: Identity) -> str:
    if not isinstance(identity, Identity):
        raise ValueError("A verified provider identity is required.")
    return hashlib.sha256(f"{identity.provider}\0{identity.subject}".encode("ascii")).hexdigest()


def writer_settings(environment, values=None):
    values = os.environ if values is None else values
    allowed = {"HOST", "NAME", "USER", "PASSWORD", "PORT", "SSLMODE", "ROOT_CERTIFICATE"}
    if any(key.startswith("FFOPT_WRITE_") and key.removeprefix("FFOPT_WRITE_DB_") not in allowed for key in values):
        raise ValueError("Unknown synthetic writer setting.")
    mapped = {
        "FFOPT_DB_" + key.removeprefix("FFOPT_WRITE_DB_"): value
        for key, value in values.items() if key.startswith("FFOPT_WRITE_DB_")
    }
    settings = DatabaseSettings.from_environment(environment, mapped)
    if environment == "stage" and settings.user != WRITER_ROLE:
        raise ValueError("Stage requires the isolated synthetic writer role.")
    return settings


def public_marker(rows, key):
    if not rows:
        return {"recorded": False, "marker": None}
    if len(rows) != 1:
        raise DatabaseUnavailable("unexpected_marker")
    row = rows[0]
    if (
        set(row) != {"owner_key", "value", "created_at"} or row["owner_key"] != key
        or row["value"] != MARKER or not isinstance(row["created_at"], datetime)
        or row["created_at"].tzinfo is None or row["created_at"].utcoffset() is None
    ):
        raise DatabaseUnavailable("unexpected_marker")
    return {
        "recorded": True,
        "marker": {"value": MARKER, "created_at": row["created_at"].astimezone(timezone.utc).isoformat()},
    }


class PostgresMarkerStore:
    def __init__(self, pool, expected_user):
        self.pool = pool
        self.expected_user = expected_user

    def open(self):
        self.pool.open(wait=False)

    def close(self):
        self.pool.close(timeout=10)

    def _check(self, connection):
        row = connection.execute(
            ROLE_CHECK, (self.expected_user, POLICY_EXPRESSION, POLICY_EXPRESSION),
        ).fetchone()
        if row is None or row.get("allowed") is not True:
            raise DatabaseUnavailable("writer_boundary_invalid")

    def check(self):
        """Readiness checks permissions and schema, never inserts or selects an owner."""
        try:
            with self.pool.connection(timeout=5) as connection:
                with connection.transaction():
                    self._check(connection)
        except (psycopg.Error, PoolClosed, PoolTimeout, TooManyRequests) as exc:
            raise DatabaseUnavailable("writer_operation_failed") from exc

    def status(self, identity):
        return self._marker(identity, record=False)

    def record(self, identity):
        return self._marker(identity, record=True)

    def _marker(self, identity, *, record):
        key = owner_key(identity)
        try:
            with self.pool.connection(timeout=5) as connection:
                with connection.transaction():
                    # A fresh SELECT snapshot sees a concurrent winner after DO NOTHING.
                    connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                    self._check(connection)
                    connection.execute("SELECT set_config('ffopt.actor', %s, true)", (key,))
                    if record:
                        connection.execute(INSERT_MARKER, (key,))
                    result = public_marker(connection.execute(SELECT_MARKER, (key,)).fetchall(), key)
                    if record and not result["recorded"]:
                        raise DatabaseUnavailable("unexpected_marker")
                return result
        except (psycopg.Error, PoolClosed, PoolTimeout, TooManyRequests) as exc:
            raise DatabaseUnavailable("writer_operation_failed") from exc


def create_writer(settings):
    pool = ConnectionPool(
        kwargs={
            "host": settings.host, "port": settings.port, "dbname": settings.database,
            "user": settings.user, "password": settings.password,
            "sslmode": settings.sslmode, "sslrootcert": settings.root_certificate,
            "connect_timeout": 5, "autocommit": True, "row_factory": dict_row,
            "options": (
                "-c statement_timeout=3000 -c lock_timeout=1000 "
                "-c idle_in_transaction_session_timeout=3000"
            ),
        },
        min_size=0, max_size=2, max_waiting=4, timeout=5, num_workers=1,
        max_idle=60, max_lifetime=300, reconnect_timeout=5, open=False,
    )
    return PostgresMarkerStore(pool, settings.user)
