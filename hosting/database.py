"""Bounded sample reads using a dedicated role; never initializes a database."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Mapping

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout, TooManyRequests

from .sample import UnexpectedSample, public_sample

ROLE_CHECK = """
SELECT (
    current_user = %s AND current_user = session_user
    AND r.rolcanlogin
    AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid)
    AND NOT has_database_privilege(current_user, current_database(), 'CREATE,TEMP')
    AND has_schema_privilege(current_user, 'hosting_probe', 'USAGE')
    AND NOT has_schema_privilege(current_user, 'hosting_probe', 'CREATE')
    AND has_table_privilege(current_user, 'hosting_probe.sample', 'SELECT')
    AND NOT has_table_privilege(
        current_user, 'hosting_probe.sample', 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
    )
) AS allowed
FROM pg_roles AS r WHERE r.rolname = current_user
"""
SAMPLE_QUERY = "SELECT id, label, value FROM hosting_probe.sample ORDER BY id LIMIT 4"


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    database: str
    user: str
    password: str = field(repr=False)
    environment: str = "stage"
    port: int = 5432
    sslmode: str = "verify-full"
    root_certificate: str = "/etc/ssl/certs/ca-certificates.crt"

    def __post_init__(self):
        if self.environment not in ("local", "stage"):
            raise ValueError("Database environment must be local or stage.")
        local_host = self.host in ("localhost", "127.0.0.1")
        azure_host = bool(re.fullmatch(r"[a-z0-9-]+\.postgres\.database\.azure\.com", self.host))
        if not local_host and not azure_host:
            raise ValueError("Use the Azure PostgreSQL hostname or a local test host.")
        if self.environment == "stage" and (not azure_host or self.port != 5432):
            raise ValueError("Stage requires its Azure PostgreSQL hostname and port 5432.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Database port must be in 1..65535.")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", name) for name in (self.database, self.user)):
            raise ValueError("Invalid database or role name.")
        if not self.password or "\0" in self.password:
            raise ValueError("Database credential must be configured.")
        if self.password.startswith("@Microsoft.KeyVault("):
            raise ValueError("Database credential reference has not resolved.")
        if self.sslmode != "verify-full" and not (
            self.environment == "local" and local_host and self.sslmode == "disable"
        ):
            raise ValueError("Certificate and hostname verification are required outside local tests.")
        if self.sslmode == "verify-full" and not Path(self.root_certificate).is_file():
            raise ValueError("Database CA certificate bundle is unavailable.")

    @classmethod
    def from_environment(
        cls, environment: str, values: Mapping[str, str] | None = None,
    ) -> "DatabaseSettings":
        values = os.environ if values is None else values
        required = ("FFOPT_DB_HOST", "FFOPT_DB_NAME", "FFOPT_DB_USER", "FFOPT_DB_PASSWORD")
        if any(not values.get(name) for name in required):
            raise ValueError("Database-readonly phase requires all database connection settings.")
        try:
            port = int(values.get("FFOPT_DB_PORT", "5432"))
        except ValueError as exc:
            raise ValueError("Database port must be an integer.") from exc
        return cls(
            host=values["FFOPT_DB_HOST"], database=values["FFOPT_DB_NAME"],
            user=values["FFOPT_DB_USER"], password=values["FFOPT_DB_PASSWORD"],
            environment=environment, port=port,
            sslmode=values.get("FFOPT_DB_SSLMODE", "verify-full"),
            root_certificate=values.get("FFOPT_DB_ROOT_CERTIFICATE", "/etc/ssl/certs/ca-certificates.crt"),
        )


class DatabaseUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__("The synthetic database probe is unavailable.")


class PostgresSampleReader:
    def __init__(self, pool: ConnectionPool, expected_user: str):
        self.pool = pool
        self.expected_user = expected_user

    def open(self):
        self.pool.open(wait=False)

    def close(self):
        self.pool.close(timeout=10)

    def sample(self) -> dict[str, object]:
        try:
            with self.pool.connection(timeout=5) as connection:
                allowed = connection.execute(ROLE_CHECK, (self.expected_user,)).fetchone()
                if allowed is None or allowed.get("allowed") is not True:
                    raise DatabaseUnavailable("role_not_readonly")
                rows = connection.execute(SAMPLE_QUERY).fetchall()
            return public_sample(rows)
        except UnexpectedSample as exc:
            raise DatabaseUnavailable("unexpected_sample") from exc
        except (psycopg.Error, PoolClosed, PoolTimeout, TooManyRequests) as exc:
            raise DatabaseUnavailable("database_operation_failed") from exc


def create_reader(settings: DatabaseSettings) -> PostgresSampleReader:
    pool = ConnectionPool(
        kwargs={
            "host": settings.host, "port": settings.port, "dbname": settings.database,
            "user": settings.user, "password": settings.password,
            "sslmode": settings.sslmode, "sslrootcert": settings.root_certificate,
            "connect_timeout": 5, "autocommit": True, "row_factory": dict_row,
            "options": (
                "-c default_transaction_read_only=on -c statement_timeout=3000 "
                "-c lock_timeout=1000 -c idle_in_transaction_session_timeout=3000"
            ),
        },
        min_size=0, max_size=2, max_waiting=4, timeout=5, num_workers=1,
        max_idle=60, max_lifetime=300, reconnect_timeout=5, open=False,
    )
    return PostgresSampleReader(pool, settings.user)
