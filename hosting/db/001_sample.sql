CREATE SCHEMA IF NOT EXISTS hosting_probe;

CREATE TABLE IF NOT EXISTS hosting_probe.sample (
    id SMALLINT PRIMARY KEY CHECK (id BETWEEN 1 AND 3),
    label VARCHAR(32) NOT NULL UNIQUE,
    value INTEGER NOT NULL CHECK (value BETWEEN 0 AND 100)
);

INSERT INTO hosting_probe.sample (id, label, value) VALUES
    (1, 'synthetic-alpha', 10),
    (2, 'synthetic-beta', 20),
    (3, 'synthetic-gamma', 30)
ON CONFLICT (id) DO NOTHING;
