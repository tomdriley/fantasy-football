import unittest
from unittest.mock import MagicMock, patch

import psycopg
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from hosting.app import Settings, create_app
from hosting.database import (
    DatabaseSettings, DatabaseUnavailable, PostgresSampleReader, ROLE_CHECK, SAMPLE_QUERY, create_reader,
)
from hosting.sample import DATASET, SAMPLE_ROWS, UnexpectedSample, public_sample


class TestPublicDatabaseSample(unittest.TestCase):
    def test_only_exact_synthetic_rows_are_returned(self):
        rows = [dict(row) for row in SAMPLE_ROWS]
        self.assertEqual(public_sample(rows), {"dataset": DATASET, "rows": rows})
        self.assertIsNot(public_sample(rows)["rows"], rows)

    def test_unexpected_contents_are_not_exposed(self):
        cases = [
            [],
            [dict(row) for row in SAMPLE_ROWS[:2]],
            [*SAMPLE_ROWS, {"id": 4, "label": "another-row", "value": 40}],
            [*reversed(SAMPLE_ROWS)],
            [{**SAMPLE_ROWS[0], "label": "not-approved"}, *SAMPLE_ROWS[1:]],
            [{**SAMPLE_ROWS[0], "private": "not-approved"}, *SAMPLE_ROWS[1:]],
            [{**SAMPLE_ROWS[0], "id": True}, *SAMPLE_ROWS[1:]],
            [{**SAMPLE_ROWS[0], "value": "10"}, *SAMPLE_ROWS[1:]],
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(UnexpectedSample) as raised:
                public_sample(rows)
            self.assertNotIn("not-approved", str(raised.exception))


class TestPostgresReaderBoundary(unittest.TestCase):
    def setUp(self):
        self.pool = MagicMock()
        self.connection = self.pool.connection.return_value.__enter__.return_value
        self.role_cursor = MagicMock()
        self.role_cursor.fetchone.return_value = {"allowed": True}
        self.sample_cursor = MagicMock()
        self.sample_cursor.fetchall.return_value = [dict(row) for row in SAMPLE_ROWS]
        self.connection.execute.side_effect = [self.role_cursor, self.sample_cursor]
        self.reader = PostgresSampleReader(self.pool, "test_reader")

    def test_reads_actual_rows_only_after_role_check(self):
        self.assertEqual(self.reader.sample(), public_sample(SAMPLE_ROWS))
        self.pool.connection.assert_called_once_with(timeout=5)
        self.assertEqual(self.connection.execute.call_args_list[0].args, (ROLE_CHECK, ("test_reader",)))
        self.assertEqual(self.connection.execute.call_args_list[1].args, (SAMPLE_QUERY,))

    def test_privileged_or_unrecognized_role_never_reads_the_table(self):
        for result in (None, {"allowed": False}, {"allowed": None}):
            with self.subTest(result=result):
                self.connection.execute.side_effect = [self.role_cursor, self.sample_cursor]
                self.role_cursor.fetchone.return_value = result
                self.connection.execute.reset_mock()
                with self.assertRaises(DatabaseUnavailable) as raised:
                    self.reader.sample()
                self.assertEqual(raised.exception.code, "role_not_readonly")
                self.assertEqual(self.connection.execute.call_count, 1)

    def test_unexpected_data_has_no_success_fallback(self):
        self.sample_cursor.fetchall.return_value = [
            {**SAMPLE_ROWS[0], "label": "private-unexpected-value"}, *SAMPLE_ROWS[1:],
        ]
        with self.assertRaises(DatabaseUnavailable) as raised:
            self.reader.sample()
        self.assertEqual(raised.exception.code, "unexpected_sample")
        self.assertNotIn("private-unexpected-value", str(raised.exception))

    def test_driver_and_pool_errors_do_not_expose_connection_details(self):
        self.connection.execute.side_effect = psycopg.OperationalError("private-connection-detail")
        with self.assertRaises(DatabaseUnavailable) as raised:
            self.reader.sample()
        self.assertNotIn("private-connection-detail", str(raised.exception))
        self.pool.connection.side_effect = PoolTimeout("private-pool-detail")
        with self.assertRaises(DatabaseUnavailable) as raised:
            self.reader.sample()
        self.assertNotIn("private-pool-detail", str(raised.exception))

    def test_lifecycle_is_explicit_and_bounded(self):
        self.reader.open()
        self.reader.close()
        self.pool.open.assert_called_once_with(wait=False)
        self.pool.close.assert_called_once_with(timeout=10)


class TestDatabaseConnectionSettings(unittest.TestCase):
    def values(self):
        return {
            "FFOPT_DB_HOST": "test-db.postgres.database.azure.com",
            "FFOPT_DB_NAME": "hosting_stage",
            "FFOPT_DB_USER": "hosting_reader",
            "FFOPT_DB_PASSWORD": "private-test-password",
        }

    def test_stage_requires_verified_tls_and_hides_credential_repr(self):
        settings = DatabaseSettings.from_environment("stage", self.values())
        self.assertEqual(settings.sslmode, "verify-full")
        self.assertNotIn("private-test-password", repr(settings))
        with patch("hosting.database.ConnectionPool") as pool:
            create_reader(settings)
        options = pool.call_args.kwargs
        self.assertEqual(options["max_size"], 2)
        self.assertEqual(options["max_waiting"], 4)
        self.assertFalse(options["open"])
        self.assertEqual(options["kwargs"]["sslmode"], "verify-full")
        self.assertEqual(options["kwargs"]["connect_timeout"], 5)
        self.assertIn("statement_timeout=3000", options["kwargs"]["options"])
        self.assertIn("default_transaction_read_only=on", options["kwargs"]["options"])

    def test_incomplete_or_unsafe_configuration_is_rejected(self):
        cases = [
            {"FFOPT_DB_PASSWORD": ""},
            {"FFOPT_DB_PASSWORD": "@Microsoft.KeyVault(SecretUri=https://example.test/secret)"},
            {"FFOPT_DB_HOST": "127.0.0.1"},
            {"FFOPT_DB_HOST": "unrelated.example"},
            {"FFOPT_DB_NAME": "database;SQL"},
            {"FFOPT_DB_PORT": "not-an-integer"},
            {"FFOPT_DB_PORT": "1234"},
            {"FFOPT_DB_SSLMODE": "prefer"},
            {"FFOPT_DB_SSLMODE": "require"},
            {"FFOPT_DB_SSLMODE": "disable"},
            {"FFOPT_DB_ROOT_CERTIFICATE": "/missing-hosting-test-ca-bundle"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                DatabaseSettings.from_environment("stage", {**self.values(), **overrides})

    def test_plaintext_postgres_is_only_an_explicit_local_fixture(self):
        settings = DatabaseSettings.from_environment("local", {
            **self.values(), "FFOPT_DB_HOST": "127.0.0.1",
            "FFOPT_DB_PORT": "15432", "FFOPT_DB_SSLMODE": "disable",
        })
        self.assertEqual(settings.port, 15432)
        self.assertEqual(settings.sslmode, "disable")
        with self.assertRaises(ValueError):
            DatabaseSettings.from_environment("local", {**self.values(), "FFOPT_DB_SSLMODE": "disable"})


class TestDatabaseHttpPhase(unittest.TestCase):
    def setUp(self):
        self.reader = MagicMock(spec=PostgresSampleReader)
        self.reader.sample.return_value = public_sample(SAMPLE_ROWS)
        self.settings = Settings(
            environment="stage", release="a" * 40,
            allowed_hosts=("stage.example.test",), phase="database-readonly",
        )

    def test_phase_page_sample_and_reader_lifecycle(self):
        with TestClient(create_app(self.settings, reader=self.reader), base_url="https://stage.example.test") as client:
            self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
            self.reader.sample.assert_not_called()
            self.assertEqual(client.get("/api/v1/advice").status_code, 404)
            self.assertEqual(client.get("/readyz").json(), {"status": "ready"})
            self.assertEqual(client.get("/fantasy-football/api/sample").json(), public_sample(SAMPLE_ROWS))
            self.assertEqual(client.get("/fantasy-football/api/status").json()["phase"], "database-readonly")
            page = client.get("/fantasy-football/").text
            self.assertIn('href="/fantasy-football/api/sample"', page)
            self.assertNotIn("No database, login", page)
        self.reader.open.assert_called_once()
        self.reader.close.assert_called_once()

    def test_database_failure_is_503_but_liveness_stays_independent(self):
        self.reader.sample.side_effect = DatabaseUnavailable("database_operation_failed")
        with TestClient(create_app(self.settings, reader=self.reader), base_url="https://stage.example.test") as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            for path in ("/readyz", "/fantasy-football/api/sample"):
                with self.assertLogs("hosting.app", level="WARNING"):
                    response = client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"], "database_unavailable")
                self.assertNotIn("rows", response.json())
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_http_writes_never_reach_the_reader(self):
        with TestClient(create_app(self.settings, reader=self.reader), base_url="https://stage.example.test") as client:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                response = client.request(method, "/fantasy-football/api/sample", json={"value": 999})
                self.assertEqual(response.status_code, 405)
            self.reader.sample.assert_not_called()

    def test_disabled_database_phase_remains_isolated(self):
        with patch("hosting.app.create_reader") as factory:
            with TestClient(create_app(Settings()), base_url="http://localhost") as client:
                self.assertEqual(client.get("/fantasy-football/api/sample").status_code, 404)
                self.assertEqual(client.get("/fantasy-football/api/status").json()["phase"], "deployment")
            factory.assert_not_called()

    def test_database_settings_cannot_silently_fall_back_to_deployment(self):
        with patch.dict("os.environ", {"FFOPT_DB_HOST": "example.postgres.database.azure.com"}, clear=True):
            with self.assertRaises(ValueError):
                create_app(Settings())
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                create_app(self.settings)
        with self.assertRaises(ValueError):
            Settings(phase="writes")
