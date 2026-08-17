"""Firmware rows: the queries behind /cohort and the JSON shape watches expect.

`source` names where a build came from. NULL is the canonical firmware and is
what a request without ?source= gets; any other value is a parallel track. Every
lookup filters on it with `source IS ?`, which binds NULL and a string alike, so
the two cases share one query.
"""

from d1 import execute, query, query_one

COLUMNS = "hardware, kind, version, url, sha256, timestamp, notes, source"

UPSERT = f"""
INSERT INTO firmwares ({COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (hardware, kind, version, COALESCE(source, '')) DO UPDATE SET
    url = excluded.url,
    sha256 = excluded.sha256,
    timestamp = excluded.timestamp,
    notes = excluded.notes
"""


def to_json(row, archival: bool = False):
    result = {
        "url": row["url"],
        "sha-256": row["sha256"],
        "friendlyVersion": row["version"],
        "timestamp": row["timestamp"],
        "notes": row["notes"] if row["notes"] else row["version"],
    }
    if archival:
        result["kind"] = row["kind"]
        result["hardware"] = row["hardware"]
        result["source"] = row["source"]
    return result


async def latest_by_kind(db, hardware, kinds, source=None):
    """Newest row per kind for one hardware and source, as {kind: row}.

    Newest is how rollback works: submit an older version with a newer
    timestamp. version breaks timestamp ties so the winner is at least stable.
    """
    placeholders = ", ".join("?" for _ in kinds)
    rows = await query(
        db,
        f"SELECT {COLUMNS} FROM ("
        f"  SELECT {COLUMNS},"
        "     ROW_NUMBER() OVER (PARTITION BY kind ORDER BY timestamp DESC, version DESC) AS rn"
        "   FROM firmwares"
        f"  WHERE hardware = ? AND source IS ? AND kind IN ({placeholders})"
        ") WHERE rn = 1",
        hardware,
        source,
        *kinds,
    )
    return {row["kind"]: row for row in rows}


async def all_rows(db, source=None):
    # hardware/version break ties on timestamp. Without them the sort is not
    # total, and Postgres and SQLite each picked a different (valid) order for
    # rows sharing a timestamp, which made the archival output unstable.
    return await query(
        db,
        f"SELECT {COLUMNS} FROM firmwares WHERE source IS ?"
        " ORDER BY kind, timestamp DESC, hardware, version",
        source,
    )


async def exists(db, hardware, kind, version, source=None):
    row = await query_one(
        db,
        "SELECT 1 FROM firmwares"
        " WHERE hardware = ? AND kind = ? AND version = ? AND source IS ? LIMIT 1",
        hardware,
        kind,
        version,
        source,
    )
    return row is not None


async def upsert(db, hardware, kind, version, url, sha256, timestamp, notes, source=None):
    await execute(db, UPSERT, hardware, kind, version, url, sha256, timestamp, notes, source)
