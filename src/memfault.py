"""The Memfault CoreDevice poll, run from the Worker's cron trigger.

Checks Memfault's releases/latest for each CoreDevice hardware, skips versions
already recorded, and for each new one downloads the .pbz while hashing it,
re-uploads it to R2, and upserts a `normal` row pointing at our own CDN URL.
Idempotent: re-running only picks up versions that are not in D1 yet.

Outbound HTTP goes through the runtime's own fetch rather than httpx: httpx
falls back to raw TCP sockets here, which the Workers runtime does not provide.
"""

import time
from urllib.parse import urlencode

from workers import fetch

import firmware
from config import var

MEMFAULT_API = "https://api.memfault.com/api/v0/releases/latest"

# Core Devices hardware revisions. These short names are both the `hardware`
# value we store in the firmwares table and the `hardware_version` string
# Memfault expects. Based on the mobileapp WatchHardwarePlatform.
CORE_DEVICES_DEVICES = (
    "asterix",
    "obelix_evt",
    "obelix_dvt",
    "obelix_pvt",
    "getafix_evt",
    "getafix_dvt",
)


class FetchError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


async def _fetch_latest(api, token, hw_revision):
    query = urlencode(
        {
            "hardware_version": hw_revision,
            "software_type": "pebbleos",
            "device_serial": "REBBLE_COHORTS_CRON",
        }
    )
    resp = await fetch(f"{api}?{query}", headers={"Memfault-Project-Key": token})
    if resp.status == 204:
        return None
    if resp.status >= 400:
        raise FetchError(resp.status)
    return await resp.json()


async def _sha256(data):
    """Hash through the runtime's WebCrypto rather than hashlib.

    Identical digest, but native code instead of hashlib compiled to WASM: 48ms
    -> 2ms for a 1.7 MB firmware, measured. Hashing is the only part of this job
    that costs real CPU — everything else is waiting on the network.
    """
    from js import Uint8Array, crypto
    from pyodide.ffi import to_js

    digest = await crypto.subtle.digest("SHA-256", to_js(data))
    return bytes(Uint8Array.new(digest).to_py()).hex()


async def _download_and_hash(url):
    """Buffer the .pbz while hashing it. Firmware images run about 2 MB against
    a 128 MB isolate, and R2 wants the body anyway."""
    resp = await fetch(url)
    if resp.status >= 400:
        raise FetchError(resp.status)
    data = await resp.bytes()
    return data, await _sha256(data)


async def _upload(env, key, data):
    from js import Object
    from pyodide.ffi import to_js

    options = to_js(
        {"httpMetadata": {"contentType": "application/octet-stream"}},
        dict_converter=Object.fromEntries,
    )
    await env.BINARIES.put(key, data, options)


async def fetch_firmware(env):
    token = var(env, "MEMFAULT_TOKEN")
    if not token:
        raise RuntimeError("MEMFAULT_TOKEN not set (npx wrangler secret put MEMFAULT_TOKEN).")
    api = var(env, "MEMFAULT_API", MEMFAULT_API)
    firmware_root = var(env, "FIRMWARE_ROOT")
    prefix = var(env, "R2_PREFIX")

    added = 0
    skipped = 0
    failed = 0
    for hardware in CORE_DEVICES_DEVICES:
        try:
            info = await _fetch_latest(api, token, hardware)
        except FetchError as e:
            print(f"[{hardware}]: lookup FAILED ({e.status})")
            failed += 1
            continue

        if info is None:
            print(f"[{hardware}]: no update available")
            continue

        version = info["version"]
        notes = info.get("notes") or None
        artifact_url = info["artifacts"][0]["url"]

        if await firmware.exists(env.DB, hardware, "normal", version):
            print(f"[{hardware}]: {version} already in DB, skipping")
            skipped += 1
            continue

        filename = f"Pebble-{version}-{hardware}.pbz"
        r2_key = f"{prefix}{hardware}/{filename}"
        public_url = f"{firmware_root}/{hardware}/{filename}"

        try:
            data, sha256 = await _download_and_hash(artifact_url)
        except FetchError as e:
            print(f"[{hardware}]: {version} download FAILED ({e.status})")
            failed += 1
            continue

        try:
            await _upload(env, r2_key, data)
        except Exception as e:  # noqa: BLE001 - R2 surfaces failures as arbitrary JS errors
            print(f"[{hardware}]: {version} upload FAILED ({e})")
            failed += 1
            continue

        await firmware.upsert(
            env.DB,
            hardware,
            "normal",
            version,
            public_url,
            sha256,
            int(time.time()),
            notes,
        )
        print(f"[{hardware}]: {version} OK ({len(data)} bytes, {sha256})")
        added += 1

    print(f"done. {added} added, {skipped} already present, {failed} failed.")
    return {"added": added, "skipped": skipped, "failed": failed}
