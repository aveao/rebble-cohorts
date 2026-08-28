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


def parse(value):
    """(major, minor, patch) for a version tag, or None if it does not parse.

    The -suffix is dropped rather than ordered: there is no total order over
    free text, and treating one as older than another would be a guess.
    """
    match = VERSION.match(value.strip()) if value else None
    return tuple(int(part or 0) for part in match.groups()[:3]) if match else None


def should_offer(offered, current):
    """Whether to hand `offered` to a watch reporting `current`.

    eng-dash offers a build only when the caller is strictly behind it and goes
    quiet at or above, which was measured against the live endpoint across the
    boundary: for a device on v4.33.2 it answered 200 for v4.33.1 and 204 for
    v4.33.3 and everything above, versions that exist nowhere included. It is
    an ordering, not a lookup, and we match it.

    Matching matters because the tracks disagree. A watch that took a newer
    build from another track and then asks the canonical endpoint is left
    alone, where offering it our older row would roll it back: this endpoint is
    authoritative, so the client installs whatever a 200 carries.

    Two cases are offered rather than withheld. A watch in recovery sends no
    current_version at all and flashes whatever it is given. And a version
    neither side can parse cannot be ordered, so it is treated as behind:
    never updating a watch we cannot read is the worse failure.

    The -suffix is not ordered, so v4.33.2-beta1 counts as v4.33.2 and is not
    offered the plain build. There is no total order over free text, and
    guessing one would be how a watch gets moved sideways by accident.
    """
    if not current:
        return True
    new, old = parse(offered), parse(current)
    if new is None or old is None:
        return True
    return old < new


def offer(row):
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
        # Always false: should_offer only lets a build through to a watch that
        # is behind it, so we never hand back something older than what the
        # watch runs and never need permission to.
        "is_downgrade": False,
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
