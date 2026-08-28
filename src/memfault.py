"""The Memfault CoreDevice poll.

Checks Memfault's releases/latest for each CoreDevice hardware and hands what
it finds to downloader.Run, which does the storing. This was what the cron ran
until the Core Devices dash endpoint appeared; core_dash.py has the canonical
track now, and this is kept as the fallback the mobile app also treats it as.
Wire it back up by adding it to CHANNELS in entry.py.
"""

from urllib.parse import urlencode

from workers import fetch

from config import var
from downloader import CORE_DEVICES_DEVICES, FetchError, Run

MEMFAULT_API = "https://api.memfault.com/api/v0/releases/latest"


async def _fetch_latest(api, token, hw_revision, serial):
    query = urlencode(
        {
            "hardware_version": hw_revision,
            "software_type": "pebbleos",
            "device_serial": serial,
        }
    )
    resp = await fetch(f"{api}?{query}", headers={"Memfault-Project-Key": token})
    if resp.status == 204:
        return None
    if resp.status >= 400:
        raise FetchError(resp.status)
    return await resp.json()


async def fetch_firmware(env):
    token = var(env, "MEMFAULT_TOKEN")
    if not token:
        raise RuntimeError("MEMFAULT_TOKEN not set (npx wrangler secret put MEMFAULT_TOKEN).")
    api = var(env, "MEMFAULT_API", MEMFAULT_API)

    run = Run(env)
    for hardware in CORE_DEVICES_DEVICES:
        try:
            info = await _fetch_latest(api, token, hardware, run.serial)
        except FetchError as e:
            run.fail(hardware, f"lookup FAILED ({e.status})")
            continue

        if info is None:
            run.log(hardware, "no update available")
            continue

        await run.publish(
            hardware,
            info["version"],
            info.get("notes") or None,
            info["artifacts"][0]["url"],
        )

    return run.summary()
