-- Adds `source`, naming where a build came from: NULL is the canonical
-- firmware, anything else ("beta", "coredevices-github", ...) is a parallel
-- track that clients opt into with ?source=.
--
-- The table is rebuilt rather than altered because the old primary key was
-- (hardware, kind, version), which would stop two sources from ever publishing
-- the same version for the same hardware. Uniqueness moves to an index over
-- COALESCE(source, ''), since SQLite treats NULLs as distinct and a plain
-- unique index would let duplicate canonical rows through.

CREATE TABLE firmwares_new (
    hardware  TEXT NOT NULL,
    kind      TEXT NOT NULL,
    version   TEXT NOT NULL,
    url       TEXT NOT NULL,
    sha256    TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    notes     TEXT,
    source    TEXT
);

INSERT INTO firmwares_new (hardware, kind, version, url, sha256, timestamp, notes, source)
SELECT hardware, kind, version, url, sha256, timestamp, notes, NULL FROM firmwares;

DROP TABLE firmwares;

ALTER TABLE firmwares_new RENAME TO firmwares;

CREATE UNIQUE INDEX ux_firmwares_identity
    ON firmwares (hardware, kind, version, COALESCE(source, ''));

-- Serves the `latest row per (hardware, kind) within a source` lookup behind
-- /cohort?select=fw.
CREATE INDEX ix_firmwares_hardware_kind_source_timestamp
    ON firmwares (hardware, kind, source, timestamp DESC);
