"""Real PostgreSQL checks against an explicitly selected local test container."""

import os
from pathlib import Path
import unittest
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from hosting.database import DatabaseSettings, DatabaseUnavailable, PostgresSampleReader, create_reader
from hosting.sample import SAMPLE_ROWS, public_sample

PORT = os.environ.get("FFOPT_TEST_POSTGRES_PORT")
ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(PORT, "Set FFOPT_TEST_POSTGRES_PORT for the isolated local PostgreSQL fixture.")
class TestRealPostgresHosting(unittest.TestCase):
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
