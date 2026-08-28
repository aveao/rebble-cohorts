"""The Core Devices dash OTA poll, run from the Worker's cron trigger.

Core Devices serve firmware for their own watches from eng-dash, at
`GET dash.repebble.com/api/ota/latest`, which is what CoreApp asks before it
falls back to Memfault. This polls it for each CoreDevice hardware and hands
what it finds to downloader.Run, which does the storing. memfault.py is still
here and still works, it is just not what the cron calls any more.

The endpoint wants a Firebase ID token for the coreapp project, and an
anonymous one is accepted: the app signs in anonymously whenever nobody is
logged in, so effectively every install carries a valid token. ID tokens last
an hour, which is exactly the cron interval, so there is nothing worth caching
between runs. We keep the refresh token of one anonymous account in the env and
exchange it for a fresh ID token at the top of every run: one extra request an
hour, and no KV entry to keep in step with the token's lifetime.

Two differences from Memfault worth knowing about:

- The response carries no timestamp, so the row gets the time we stored it,
  same as the Memfault path. That is what makes a Core Devices downgrade work
  by itself: `latest` here is newest-timestamp, so a version that goes
  backwards upstream still wins on the way out.
- The endpoint is authoritative about what to install, and answers 204 when the
  caller is already current. We never send `current_version`, so it always
  offers the newest build on this account's track and the D1 check decides
  whether it is new to us.
"""

from urllib.parse import urlencode

from workers import fetch

from config import var
from downloader import CORE_DEVICES_DEVICES, FetchError, Run

CORE_DASH_API = "https://dash.repebble.com/api/ota/latest"
FIREBASE_TOKEN_API = "https://securetoken.googleapis.com/v1/token"


async def _id_token(api_key, refresh_token):
    """Exchange the stored refresh token for an ID token good for an hour.

    Firebase hands back a refresh token of its own here. We ignore it: refresh
    tokens do not expire, the old one keeps working, and a Worker cannot write
    to its own env to store a new one anyway.
    """
    resp = await fetch(
        f"{FIREBASE_TOKEN_API}?key={api_key}",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}),
    )
    if resp.status >= 400:
        raise FetchError(resp.status)
    return (await resp.json())["id_token"]


async def _fetch_latest(api, token, hw_revision, serial):
    query = urlencode({"hardware_version": hw_revision, "device_serial": serial})
    resp = await fetch(f"{api}?{query}", headers={"Authorization": f"Bearer {token}"})
    if resp.status == 204:
        return None
    if resp.status >= 400:
        raise FetchError(resp.status)
    return await resp.json()


def _artifact_url(info, hardware):
    """The .pbz for this hardware.

    The client only ever reads artifacts[0] and expects the matching one to be
    first, so a response with several is already unusual; pick by
    hardware_version anyway and fall back to the first.
    """
    artifacts = info.get("artifacts") or []
    for artifact in artifacts:
        if artifact.get("hardware_version") == hardware and artifact.get("url"):
            return artifact["url"]
    return artifacts[0]["url"] if artifacts else None


async def fetch_firmware(env):
    refresh_token = var(env, "CORE_DASH_REFRESH_TOKEN")
    if not refresh_token:
        raise RuntimeError(
            "CORE_DASH_REFRESH_TOKEN not set "
            "(npx wrangler secret put CORE_DASH_REFRESH_TOKEN; see the README for minting one)."
        )
    api_key = var(env, "CORE_DASH_FIREBASE_KEY")
    api = var(env, "CORE_DASH_API", CORE_DASH_API)

    try:
        token = await _id_token(api_key, refresh_token)
    except FetchError as e:
        # Nothing device-specific to report: without a token every lookup 401s,
        # so stop here rather than logging the same failure six times.
        raise RuntimeError(f"could not refresh the anonymous Firebase token ({e.status})") from None

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

        artifact_url = _artifact_url(info, hardware)
        if not artifact_url:
            run.fail(hardware, f"{info.get('version')} offered with no artifact")
            continue

        await run.publish(hardware, info["version"], info.get("notes") or None, artifact_url)

    return run.summary()
