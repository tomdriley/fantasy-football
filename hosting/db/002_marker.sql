-- Admin-only, one-time migration in hosting_stage. Never run by application startup.
-- Provision ffopt_stage_probe_writer LOGIN separately with a private, rotated credential:
-- NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS, no memberships.
-- The existing database must already deny PUBLIC CREATE/TEMP; do not change reader grants.
BEGIN;

CREATE SCHEMA hosting_write_probe;
REVOKE ALL ON SCHEMA hosting_write_probe FROM PUBLIC;

CREATE TABLE hosting_write_probe.marker (
    owner_key TEXT PRIMARY KEY CHECK (owner_key ~ '^[0-9a-f]{64}$'),
    value TEXT NOT NULL DEFAULT 'synthetic-write-v1' CHECK (value = 'synthetic-write-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
REVOKE ALL ON hosting_write_probe.marker FROM PUBLIC;
ALTER TABLE hosting_write_probe.marker ENABLE ROW LEVEL SECURITY;
ALTER TABLE hosting_write_probe.marker FORCE ROW LEVEL SECURITY;
CREATE POLICY marker_owner ON hosting_write_probe.marker
    USING (owner_key = current_setting('ffopt.actor', true))
    WITH CHECK (owner_key = current_setting('ffopt.actor', true));

GRANT CONNECT ON DATABASE hosting_stage TO ffopt_stage_probe_writer;
GRANT USAGE ON SCHEMA hosting_write_probe TO ffopt_stage_probe_writer;
GRANT SELECT, INSERT ON hosting_write_probe.marker TO ffopt_stage_probe_writer;

COMMIT;
