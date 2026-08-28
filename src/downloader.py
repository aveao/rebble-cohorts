"""What the firmware pollers have in common.

memfault.py and core_dash.py differ only in how they ask an upstream what the
newest build for a hardware is. Everything downstream of that answer is the
same, and lives here: skip a version already recorded, download the .pbz while
hashing it, re-upload it to R2, and upsert a `normal` row pointing at our own
CDN URL. Idempotent, so a run where nothing has been published does almost no
work.

Outbound HTTP goes through the runtime's own fetch rather than httpx: httpx
falls back to raw TCP sockets here, which the Workers runtime does not provide.
"""

import time

from workers import fetch

import firmware
from config import var

# Core Devices hardware revisions. These short names are both the `hardware`
# value we store in the firmwares table and the `hardware_version` string the
# upstreams expect. Based on the mobileapp WatchHardwarePlatform.
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


async def _sha256(data):
    """Hash through the runtime's WebCrypto rather than hashlib.

    Identical digest, but native code instead of hashlib compiled to WASM: 48ms
    -> 2ms for a 1.7 MB firmware, measured. Hashing is the only part of this job
    that costs real CPU, everything else is waiting on the network.
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


class Run:
    """One pass over every device: the resolved config, plus the tally.

    A poller works out the newest version for a hardware and hands it to
    publish(); storing it, logging it and counting what happened are the same
    whichever upstream the answer came from.
    """

    def __init__(self, env):
        self.env = env
        self.firmware_root = var(env, "FIRMWARE_ROOT")
        self.prefix = var(env, "R2_PREFIX")
        # The serial both upstreams are told we are. Memfault only logs it;
        # the Core Devices dash uses it to pick which device it is answering
        # for, so pointing the cron at a different one is a config change.
        self.serial = var(env, "DEVICE_SERIAL")
        self.added = 0
        self.skipped = 0
        self.failed = 0

    def log(self, hardware, message):
        print(f"[{hardware}]: {message}")

    def fail(self, hardware, message):
        self.failed += 1
        self.log(hardware, message)

    async def publish(self, hardware, version, notes, artifact_url):
        if await firmware.exists(self.env.DB, hardware, "normal", version):
            self.skipped += 1
            self.log(hardware, f"{version} already in DB, skipping")
            return

        filename = f"Pebble-{version}-{hardware}.pbz"
        r2_key = f"{self.prefix}{hardware}/{filename}"
        public_url = f"{self.firmware_root}/{hardware}/{filename}"

        try:
            data, sha256 = await _download_and_hash(artifact_url)
        except FetchError as e:
            self.fail(hardware, f"{version} download FAILED ({e.status})")
            return

        try:
            await _upload(self.env, r2_key, data)
        except Exception as e:  # noqa: BLE001 - R2 surfaces failures as arbitrary JS errors
            self.fail(hardware, f"{version} upload FAILED ({e})")
            return

        await firmware.upsert(
            self.env.DB,
            hardware,
            "normal",
            version,
            public_url,
            sha256,
            int(time.time()),
            notes,
            size=len(data),
        )
        self.added += 1
        self.log(hardware, f"{version} OK ({len(data)} bytes, {sha256})")

    def summary(self):
        print(f"done. {self.added} added, {self.skipped} already present, {self.failed} failed.")
        return {"added": self.added, "skipped": self.skipped, "failed": self.failed}
