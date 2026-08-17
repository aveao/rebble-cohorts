-- Mirrors the Postgres schema this service ran on before D1, so the rows
-- exported from Postgres import as-is.
CREATE TABLE firmwares (
    hardware  TEXT NOT NULL,
    kind      TEXT NOT NULL,
    version   TEXT NOT NULL,
    url       TEXT NOT NULL,
    sha256    TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    notes     TEXT,
    PRIMARY KEY (hardware, kind, version)
);

-- Serves the `latest row per (hardware, kind)` lookup behind /cohort?select=fw.
CREATE INDEX ix_firmwares_hardware_kind_timestamp
    ON firmwares (hardware, kind, timestamp DESC);
