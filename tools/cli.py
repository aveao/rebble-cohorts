#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["click"]
# ///
"""Local admin commands for the cohorts D1 database.

These used to be `flask import_json` / `flask submit_firmware`, which ran inside
the app. A Worker has no CLI, so they run here instead and emit SQL you feed to
wrangler:

    uv run tools/cli.py import_json > seed.sql
    npx wrangler d1 execute cohorts --remote --file=seed.sql

Drop --remote to apply against the local dev database instead.
"""

import json
import sys
import time

import click

VALID_FW_KINDS = ("normal", "recovery")
DEFAULT_FIRMWARE_ROOT = "https://cohorts-storage.lavate.ch/fw"

UPSERT = """INSERT INTO firmwares
    (hardware, kind, version, url, sha256, timestamp, notes, source, size)
VALUES ({values})
ON CONFLICT (hardware, kind, version, COALESCE(source, '')) DO UPDATE SET
    url = excluded.url, sha256 = excluded.sha256,
    timestamp = excluded.timestamp, notes = excluded.notes,
    size = excluded.size;"""


def _literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _upsert_sql(hardware, kind, version, url, sha256, timestamp, notes, source=None, size=None):
    values = ", ".join(
        _literal(v) for v in (hardware, kind, version, url, sha256, timestamp, notes, source, size)
    )
    return UPSERT.format(values=values)


@click.group()
def cli():
    """Emit SQL for the cohorts D1 database."""


@cli.command(name="import_json")
@click.option("--firmware-root", default=DEFAULT_FIRMWARE_ROOT, help="Base URL for .pbz files.")
@click.argument("config_path", type=click.Path(exists=True), default="config.json")
def import_json_command(firmware_root, config_path):
    """Seed rows from config.json. Re-running is idempotent."""
    with open(config_path) as f:
        fw_config = json.load(f)

    notes_map = fw_config.get("notes", {})
    timestamps_map = fw_config.get("timestamps", {})
    count = 0
    for hardware, variants in fw_config["hardware"].items():
        for kind, entry in variants.items():
            raw_version = entry["version"]
            url = f"{firmware_root}/{hardware}/Pebble-{raw_version}-{hardware}.pbz"
            click.echo(
                _upsert_sql(
                    hardware,
                    kind,
                    f"v{raw_version}",
                    url,
                    entry["sha-256"],
                    timestamps_map[raw_version],
                    notes_map.get(raw_version),
                )
            )
            count += 1
    click.echo(f"-- {count} firmware rows", err=True)


@cli.command(name="submit_firmware")
@click.argument("hardware")
@click.argument("kind")
@click.argument("version")
@click.argument("url")
@click.argument("sha256")
@click.option("--timestamp", type=int, default=None, help="Unix timestamp (default: now).")
@click.option("--notes", default=None, help="Release notes (optional).")
@click.option(
    "--source",
    default=None,
    help="Build track, e.g. 'beta'. Omit for the canonical firmware, which is what "
    "/cohort serves when no ?source= is given.",
)
@click.option(
    "--size",
    type=int,
    default=None,
    help="Size of the .pbz in bytes, reported as file_size by /api/ota/latest. "
    "Omit if unknown; the client ignores the field.",
)
def submit_firmware_command(hardware, kind, version, url, sha256, timestamp, notes, source, size):
    """Add or update a single firmware row."""
    if kind not in VALID_FW_KINDS:
        raise click.BadParameter(f"kind must be one of {VALID_FW_KINDS}, got {kind!r}")
    if timestamp is None:
        timestamp = int(time.time())
    click.echo(_upsert_sql(hardware, kind, version, url, sha256, timestamp, notes, source, size))


if __name__ == "__main__":
    sys.exit(cli())
