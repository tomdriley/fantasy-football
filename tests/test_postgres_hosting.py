"""Real PostgreSQL checks against an explicitly selected local test container."""

import os
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from hosting.database import DatabaseSettings, DatabaseUnavailable, PostgresSampleReader, create_reader
from hosting.sample import SAMPLE_ROWS, public_sample
from hosting.auth import Identity
from hosting.write_database import (
    INSERT_MARKER, PostgresMarkerStore, create_writer, owner_key,
)

PORT = os.environ.get("FFOPT_TEST_POSTGRES_PORT")
ROOT = Path(__file__).resolve().parent.parent


class PostgresFixture:
    @classmethod
    def setUpClass(cls):
        port = int(PORT)
        if not 1 <= port <= 65535:
            raise ValueError("The test PostgreSQL port must be in 1..65535.")
        cls.root_options = {
            "host": "127.0.0.1", "hostaddr": "127.0.0.1", "port": port,
            "dbname": "postgres", "user": "postgres", "password": "local-test-only",
            "sslmode": "disable", "connect_timeout": 3, "autocommit": True,
            "options": "-c statement_timeout=3000 -c lock_timeout=1000",
            "row_factory": dict_row,
        }
        cls.root = psycopg.connect(**cls.root_options)
        cls.addClassCleanup(cls.root.close)
        suffix = uuid.uuid4().hex[:12]
        cls.database = "hosting_test_" + suffix
        cls.username = "hosting_reader_" + suffix
        cls.root.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(cls.username), sql.Literal("reader-test-only"),
        ))
        cls.addClassCleanup(
            cls.root.execute, sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.username)),
        )
        cls.root.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database)))
        cls.addClassCleanup(
            cls.root.execute,
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(cls.database)),
        )
        cls.admin = psycopg.connect(**{**cls.root_options, "dbname": cls.database})
        cls.addClassCleanup(cls.admin.close)
        with cls.admin.transaction():
            cls.admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(cls.database),
            ))
            cls.admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            cls.admin.execute((ROOT / "hosting/db/001_sample.sql").read_text(), prepare=False)
            cls.admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(cls.database), sql.Identifier(cls.username),
            ))
            cls.admin.execute(sql.SQL("GRANT USAGE ON SCHEMA hosting_probe TO {}").format(
                sql.Identifier(cls.username),
            ))
            cls.admin.execute(sql.SQL("GRANT SELECT ON hosting_probe.sample TO {}").format(
                sql.Identifier(cls.username),
            ))
        cls.reader_options = {
            **cls.root_options, "dbname": cls.database,
            "user": cls.username, "password": "reader-test-only",
        }
        cls.reader = create_reader(DatabaseSettings(
            host="127.0.0.1", database=cls.database, user=cls.username,
            password="reader-test-only", environment="local", port=port, sslmode="disable",
        ))
        cls.pool = cls.reader.pool
        cls.reader.open()
        cls.addClassCleanup(cls.reader.close)


@unittest.skipUnless(PORT, "Set FFOPT_TEST_POSTGRES_PORT for the isolated local PostgreSQL fixture.")
class TestRealPostgresHosting(PostgresFixture, unittest.TestCase):
    def test_exact_rows_are_read_from_real_postgres(self):
        self.assertEqual(self.reader.sample(), public_sample(SAMPLE_ROWS))

    def test_every_write_and_ddl_probe_is_denied_by_grants(self):
        operations = [
            "INSERT INTO hosting_probe.sample VALUES (4, 'blocked', 40)",
            "UPDATE hosting_probe.sample SET value = 11 WHERE id = 1",
            "DELETE FROM hosting_probe.sample WHERE id = 1",
            "TRUNCATE hosting_probe.sample",
            "CREATE TABLE hosting_probe.blocked (id INTEGER)",
            "CREATE TABLE public.blocked (id INTEGER)",
            "CREATE TEMP TABLE blocked (id INTEGER)",
            "CREATE SCHEMA blocked",
        ]
        with psycopg.connect(**self.reader_options) as connection:
            connection.execute("SET default_transaction_read_only = off")
            for statement in operations:
                with self.subTest(statement=statement):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege) as raised:
                        connection.execute(statement)
                    self.assertEqual(raised.exception.sqlstate, "42501")
        self.assertEqual(self.reader.sample(), public_sample(SAMPLE_ROWS))

    def test_unauthorized_data_is_not_returned(self):
        self.addCleanup(self.admin.execute, "UPDATE hosting_probe.sample SET label = 'synthetic-alpha' WHERE id = 1")
        self.admin.execute("UPDATE hosting_probe.sample SET label = 'not-public-data' WHERE id = 1")
        with self.assertRaises(DatabaseUnavailable) as raised:
            self.reader.sample()
        self.assertEqual(raised.exception.code, "unexpected_sample")
        self.assertNotIn("not-public-data", str(raised.exception))

    def test_inherited_privileges_are_rejected(self):
        self.addCleanup(
            self.root.execute,
            sql.SQL("REVOKE pg_read_all_data FROM {}").format(sql.Identifier(self.username)),
        )
        self.root.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(sql.Identifier(self.username)))
        with self.assertRaises(DatabaseUnavailable) as raised:
            self.reader.sample()
        self.assertEqual(raised.exception.code, "role_not_readonly")

    def test_admin_credentials_are_rejected_by_reader_boundary(self):
        pool = ConnectionPool(
            kwargs={**self.root_options, "dbname": self.database},
            min_size=0, max_size=1, open=False,
        )
        reader = PostgresSampleReader(pool, "postgres")
        reader.open()
        self.addCleanup(reader.close)
        with self.assertRaises(DatabaseUnavailable) as raised:
            reader.sample()
        self.assertEqual(raised.exception.code, "role_not_readonly")

    def test_new_pool_reads_persisted_rows(self):
        reader = PostgresSampleReader(
            ConnectionPool(kwargs=self.reader_options, min_size=0, max_size=1, open=False),
            self.username,
        )
        reader.open()
        self.addCleanup(reader.close)
        self.assertEqual(reader.sample(), public_sample(SAMPLE_ROWS))

    def test_wrong_password_fails_without_a_sample_fallback(self):
        options = {**self.reader_options, "password": "incorrect-test-only"}
        reader = PostgresSampleReader(
            ConnectionPool(kwargs=options, min_size=0, max_size=1, open=False, reconnect_timeout=2),
            self.username,
        )
        reader.open()
        self.addCleanup(reader.close)
        with self.assertLogs("psycopg.pool", level="WARNING") as logs:
            with self.assertRaises(DatabaseUnavailable) as raised:
                reader.sample()
        self.assertEqual(raised.exception.code, "database_operation_failed")
        self.assertNotIn("incorrect-test-only", str(raised.exception))
        self.assertNotIn("incorrect-test-only", "\n".join(logs.output))


@unittest.skipUnless(PORT, "Set FFOPT_TEST_POSTGRES_PORT for the isolated local PostgreSQL fixture.")
class TestRealPostgresWriter(PostgresFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.writer_user = "hosting_writer_" + uuid.uuid4().hex[:12]
        cls.root.execute(sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        ).format(sql.Identifier(cls.writer_user), sql.Literal("writer-test-only")))
        cls.addClassCleanup(cls.root.execute, sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.writer_user)))
        cls.addClassCleanup(cls.admin.execute, sql.SQL("DROP OWNED BY {}").format(sql.Identifier(cls.writer_user)))
        migration = (ROOT / "hosting/db/002_marker.sql").read_text()
        cls.admin.execute(
            migration.replace("hosting_stage", cls.database).replace("ffopt_stage_probe_writer", cls.writer_user),
            prepare=False,
        )
        cls.writer_config = DatabaseSettings(
            host="127.0.0.1", database=cls.database, user=cls.writer_user,
            password="writer-test-only", environment="local", port=int(PORT), sslmode="disable",
        )
        cls.writer_options = {
            **cls.root_options, "dbname": cls.database, "user": cls.writer_user, "password": "writer-test-only",
        }
        cls.writer = create_writer(cls.writer_config)
        cls.writer.open()
        cls.addClassCleanup(cls.writer.close)

    def setUp(self):
        self.owner = Identity("google", "synthetic-" + uuid.uuid4().hex)
        self.other = Identity("google", "synthetic-" + uuid.uuid4().hex)

    def test_first_write_and_sequential_duplicates_return_one_original_marker(self):
        self.assertEqual(self.writer.status(self.owner), {"recorded": False, "marker": None})
        first = self.writer.record(self.owner)
        self.assertTrue(first["recorded"])
        self.assertEqual(first["marker"]["value"], "synthetic-write-v1")
        for _ in range(3):
            self.assertEqual(self.writer.record(self.owner), first)
        self.assertEqual(self.writer.status(self.owner), first)
        self.assertEqual(self.admin.execute(
            "SELECT count(*) AS count FROM hosting_write_probe.marker WHERE owner_key = %s",
            (owner_key(self.owner),),
        ).fetchone()["count"], 1)
        self.assertEqual(self.reader.sample(), public_sample(SAMPLE_ROWS))

    def test_concurrent_identical_inserts_return_same_record(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(executor.map(self.writer.record, [self.owner] * 4))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(self.admin.execute(
            "SELECT count(*) AS count FROM hosting_write_probe.marker WHERE owner_key = %s",
            (owner_key(self.owner),),
        ).fetchone()["count"], 1)

    def test_status_readiness_and_other_owner_never_create_or_disclose_rows(self):
        initial_count = self.admin.execute("SELECT count(*) AS count FROM hosting_write_probe.marker").fetchone()
        self.writer.check()
        self.assertFalse(self.writer.status(self.owner)["recorded"])
        self.assertEqual(self.admin.execute("SELECT count(*) AS count FROM hosting_write_probe.marker").fetchone(), initial_count)
        first = self.writer.record(self.owner)
        self.assertFalse(self.writer.status(self.other)["recorded"])
        self.assertEqual(self.writer.status(self.owner), first)
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(self.writer.status, [self.owner, self.other])), [
                first, {"recorded": False, "marker": None},
            ])

    def test_rls_hides_other_owner_even_without_where_and_blocks_cross_owner_insert(self):
        self.writer.record(self.owner)
        with psycopg.connect(**self.writer_options) as connection:
            self.assertEqual(connection.execute("SELECT * FROM hosting_write_probe.marker").fetchall(), [])
            with connection.transaction():
                connection.execute("SELECT set_config('ffopt.actor', %s, true)", (owner_key(self.other),))
                self.assertEqual(connection.execute("SELECT * FROM hosting_write_probe.marker").fetchall(), [])
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute(INSERT_MARKER, (owner_key(self.owner),))
            self.assertEqual(connection.execute("SELECT * FROM hosting_write_probe.marker").fetchall(), [])

    def test_pool_actor_is_reset_on_success_and_rollback(self):
        pool = ConnectionPool(kwargs=self.writer_options, min_size=0, max_size=1, open=False)
        writer = PostgresMarkerStore(pool, self.writer_user)
        writer.open()
        self.addCleanup(writer.close)
        writer.record(self.owner)
        with pool.connection() as connection:
            self.assertIn(connection.execute("SELECT current_setting('ffopt.actor', true) AS actor").fetchone()["actor"], (None, ""))
            self.assertEqual(connection.execute("SELECT * FROM hosting_write_probe.marker").fetchall(), [])
        self.admin.execute(
            "INSERT INTO hosting_write_probe.marker (owner_key, created_at) VALUES (%s, 'infinity')",
            (owner_key(self.other),),
        )
        with self.assertRaises(DatabaseUnavailable):
            writer.status(self.other)
        with pool.connection() as connection:
            self.assertIn(connection.execute("SELECT current_setting('ffopt.actor', true) AS actor").fetchone()["actor"], (None, ""))
            self.assertEqual(connection.execute("SELECT * FROM hosting_write_probe.marker").fetchall(), [])
        self.assertTrue(writer.status(self.owner)["recorded"])

    def test_new_pool_and_new_process_preserve_original_timestamp(self):
        first = self.writer.record(self.owner)
        writer = create_writer(self.writer_config)
        writer.open()
        self.addCleanup(writer.close)
        self.assertEqual(writer.record(self.owner), first)
        code = """
import json, sys
from hosting.auth import Identity
from hosting.database import DatabaseSettings
from hosting.write_database import create_writer
values = json.loads(sys.stdin.read())
writer = create_writer(DatabaseSettings(**values['settings']))
writer.open()
try:
    print(json.dumps(writer.record(Identity('google', values['subject']))))
finally:
    writer.close()
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            input=json.dumps({
                "settings": {
                    "host": "127.0.0.1", "database": self.database, "user": self.writer_user,
                    "password": "writer-test-only", "environment": "local", "port": int(PORT), "sslmode": "disable",
                },
                "subject": self.owner.subject,
            }),
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), first)

    def test_writer_cannot_read_sample_write_elsewhere_or_run_ddl(self):
        operations = [
            "SELECT * FROM hosting_probe.sample",
            "INSERT INTO hosting_probe.sample VALUES (4, 'blocked', 40)",
            "UPDATE hosting_probe.sample SET value = 11 WHERE id = 1",
            "UPDATE hosting_write_probe.marker SET value = 'blocked'",
            "DELETE FROM hosting_write_probe.marker", "TRUNCATE hosting_write_probe.marker",
            "CREATE TABLE hosting_write_probe.blocked (id INTEGER)",
            "ALTER TABLE hosting_write_probe.marker DISABLE ROW LEVEL SECURITY",
            "CREATE TABLE public.blocked (id INTEGER)", "CREATE TEMP TABLE blocked (id INTEGER)",
            "CREATE SCHEMA blocked", "CREATE ROLE blocked",
        ]
        with psycopg.connect(**self.writer_options) as connection:
            for statement in operations:
                with self.subTest(statement=statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(statement)
        self.assertEqual(self.reader.sample(), public_sample(SAMPLE_ROWS))

    def test_reader_is_unchanged_and_has_no_access_to_marker(self):
        with psycopg.connect(**self.reader_options) as connection:
            for query in ("SELECT * FROM hosting_write_probe.marker", INSERT_MARKER):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(query, (owner_key(self.owner),) if query == INSERT_MARKER else None)
        self.assertEqual(self.reader.sample(), public_sample(SAMPLE_ROWS))

    def test_excess_table_column_membership_and_grant_option_privileges_fail_closed(self):
        grant_cases = [
            ("GRANT UPDATE ON hosting_write_probe.marker TO {}", "REVOKE UPDATE ON hosting_write_probe.marker FROM {}"),
            ("GRANT UPDATE (value) ON hosting_write_probe.marker TO {}", "REVOKE UPDATE (value) ON hosting_write_probe.marker FROM {}"),
            ("GRANT MAINTAIN ON hosting_write_probe.marker TO {}", "REVOKE MAINTAIN ON hosting_write_probe.marker FROM {}"),
            ("GRANT SELECT ON hosting_probe.sample TO {}", "REVOKE SELECT ON hosting_probe.sample FROM {}"),
            ("GRANT INSERT ON hosting_probe.sample TO {}", "REVOKE INSERT ON hosting_probe.sample FROM {}"),
            ("GRANT CREATE ON SCHEMA public TO {}", "REVOKE CREATE ON SCHEMA public FROM {}"),
            ("GRANT SELECT ON hosting_write_probe.marker TO {} WITH GRANT OPTION",
             "REVOKE GRANT OPTION FOR SELECT ON hosting_write_probe.marker FROM {}"),
            ("GRANT SELECT (value) ON hosting_write_probe.marker TO {} WITH GRANT OPTION",
             "REVOKE GRANT OPTION FOR SELECT (value) ON hosting_write_probe.marker FROM {}"),
            ("GRANT pg_read_all_data TO {}", "REVOKE pg_read_all_data FROM {}"),
        ]
        for grant, revoke in grant_cases:
            with self.subTest(grant=grant):
                self.admin.execute(sql.SQL(grant).format(sql.Identifier(self.writer_user)))
                try:
                    with self.assertRaises(DatabaseUnavailable) as raised:
                        self.writer.record(self.owner)
                    self.assertEqual(raised.exception.code, "writer_boundary_invalid")
                finally:
                    self.admin.execute(sql.SQL(revoke).format(sql.Identifier(self.writer_user)))
        self.writer.check()
        self.assertFalse(self.writer.status(self.owner)["recorded"])

    def test_rls_policy_force_and_server_defaults_drift_fail_closed(self):
        changes = [
            ("ALTER TABLE hosting_write_probe.marker DISABLE ROW LEVEL SECURITY",
             "ALTER TABLE hosting_write_probe.marker ENABLE ROW LEVEL SECURITY"),
            ("ALTER TABLE hosting_write_probe.marker NO FORCE ROW LEVEL SECURITY",
             "ALTER TABLE hosting_write_probe.marker FORCE ROW LEVEL SECURITY"),
            ("CREATE POLICY bypass ON hosting_write_probe.marker USING (true)",
             "DROP POLICY bypass ON hosting_write_probe.marker"),
            ("ALTER POLICY marker_owner ON hosting_write_probe.marker USING (true)",
             "ALTER POLICY marker_owner ON hosting_write_probe.marker USING (owner_key = current_setting('ffopt.actor', true))"),
            ("ALTER TABLE hosting_write_probe.marker ALTER COLUMN created_at SET DEFAULT '2020-01-01'::timestamptz",
             "ALTER TABLE hosting_write_probe.marker ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP"),
            ("ALTER TABLE hosting_write_probe.marker ALTER COLUMN value SET DEFAULT 'unexpected'",
             "ALTER TABLE hosting_write_probe.marker ALTER COLUMN value SET DEFAULT 'synthetic-write-v1'"),
        ]
        for change, restore in changes:
            with self.subTest(change=change):
                self.admin.execute(change)
                try:
                    with self.assertRaises(DatabaseUnavailable) as raised:
                        self.writer.record(self.owner)
                    self.assertEqual(raised.exception.code, "writer_boundary_invalid")
                finally:
                    self.admin.execute(restore)
        self.writer.check()

    def test_admin_and_missing_table_or_revoked_grants_are_unavailable(self):
        writer = PostgresMarkerStore(
            ConnectionPool(kwargs={**self.root_options, "dbname": self.database}, min_size=0, max_size=1, open=False),
            "postgres",
        )
        writer.open()
        self.addCleanup(writer.close)
        with self.assertRaises(DatabaseUnavailable):
            writer.record(self.owner)
        self.admin.execute("ALTER TABLE hosting_write_probe.marker RENAME TO hidden_marker")
        try:
            with self.assertRaises(DatabaseUnavailable):
                self.writer.status(self.owner)
        finally:
            self.admin.execute("ALTER TABLE hosting_write_probe.hidden_marker RENAME TO marker")
        self.admin.execute(sql.SQL("REVOKE INSERT ON hosting_write_probe.marker FROM {}").format(sql.Identifier(self.writer_user)))
        try:
            with self.assertRaises(DatabaseUnavailable):
                self.writer.record(self.owner)
        finally:
            self.admin.execute(sql.SQL("GRANT INSERT ON hosting_write_probe.marker TO {}").format(sql.Identifier(self.writer_user)))

    def test_closed_pool_and_bad_password_fail_without_success(self):
        writer = create_writer(self.writer_config)
        writer.open()
        writer.close()
        with self.assertRaises(DatabaseUnavailable):
            writer.record(self.owner)
        bad = PostgresMarkerStore(ConnectionPool(
            kwargs={**self.writer_options, "password": "incorrect-writer-test-only"},
            min_size=0, max_size=1, open=False, reconnect_timeout=2,
        ), self.writer_user)
        bad.open()
        self.addCleanup(bad.close)
        with self.assertLogs("psycopg.pool", level="WARNING") as logs:
            with self.assertRaises(DatabaseUnavailable) as raised:
                bad.record(self.owner)
        self.assertEqual(raised.exception.code, "writer_operation_failed")
        self.assertNotIn("incorrect-writer-test-only", "\n".join(logs.output))
