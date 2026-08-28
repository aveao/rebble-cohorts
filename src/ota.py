"""The eng-dash `/ota/latest` shape, served from our own firmware rows.

This is the mirror image of core_dash.py: that one reads Core Devices' endpoint,
this one answers in the same shape, so a client pointed at us cannot tell the
difference. Two parts of that contract drive everything here:

- On 200 the client does not compare versions, it installs what we hand it. The
  server is authoritative, so "already current" has to be answered with 204
  rather than with the version the watch is already running.
- An older build is refused unless the response says `is_downgrade`, which is
  what permits the reboot into PRF. Rollback in cohorts is exactly that, an
  older version published with a newer timestamp, so we work the flag out by
  comparing what we are about to offer against what the watch says it runs.
"""

import re

# The regex the client parses `version` with (SystemService.kt:258), anchored.
# The v prefix, the patch and a -suffix are all optional.
VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-(.*))?$")


def normalise(value):
    """A version tag without its optional `v`, for comparing two of them.

    The client v-prefixes `current_version` on the way out, so what it sends
    and what we store can differ by nothing but that.
    """
    return value.strip().removeprefix("v") if value else ""


def parse(value):
    """(major, minor, patch) for a version tag, or None if it does not parse.

    The -suffix is dropped rather than ordered: there is no total order over
    free text, and treating one as older than another would be a guess.
    """
    match = VERSION.match(value.strip()) if value else None
    return tuple(int(part or 0) for part in match.groups()[:3]) if match else None


def is_downgrade(offered, current):
    """Whether `offered` is an older build than the one the watch reports.

    False whenever either side is missing or unparseable, including the PRF
    shape where the client sends no current_version at all: a watch in recovery
    flashes whatever it is given, and guessing here could only ever withhold
    permission the client turns out to need.
    """
    new, old = parse(offered), parse(current)
    return new is not None and old is not None and new < old


def offer(row, current_version=None):
    """One firmware row as an /ota/latest 200 body.

    Every field the real endpoint sends is sent, including the ones the client
    documents as ignored: it deserialises the whole object, so a field missing
    here is a field its parser may still insist on.
    """
    version = row["version"]
    return {
        "version": version,
        "display_name": version,
        "notes": row["notes"],
        "is_downgrade": is_downgrade(version, current_version),
        # We have no notion of a release that must be installed on the way
        # past, and the client ignores the flag anyway.
        "must_pass_through": False,
        "artifacts": [
            {
                "url": row["url"],
                # The client always writes download.pbz and ignores this; the
                # tail of our own URL is the honest answer.
                "filename": row["url"].rsplit("/", 1)[-1],
                "file_size": row["size"] or 0,
                "sha256": row["sha256"],
                "hardware_version": row["hardware"],
            }
        ],
    }
