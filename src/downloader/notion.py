"""The notion channel: PebbleOS releases straight from the GitHub repo.

Core Devices announce a build on their changelog well before it reaches the OTA
track our anonymous account resolves to, and the .pbz files are attached to the
matching GitHub release the whole time. This poller closes that gap: it reads
the top row of the changelog table, takes the version out of it, and tries the
release asset for each of our hardware revisions.

The track is named after where the version comes from rather than after how
stable it is: these are the published releases, they just reach GitHub before
they reach the OTA track. Rows land with source='notion', so
`/cohort?select=fw&hardware=...&source=notion` serves them and the canonical
track is untouched. Blobs get a `github-` prefix on their R2 key, naming where
the bytes came from, since the same version can exist on both tracks.

Two things worth knowing:

- The changelog is a Notion page. There is no Notion token here and none is
  needed: a published page answers loadPageChunk unauthenticated, which is the
  same call the site's own frontend makes.
- The asset URL is constructed, not advertised, so a hardware with no build in
  a given release is an ordinary 404 rather than a fault, and Run counts those
  separately from failures.
"""

import json
import re

from workers import fetch

from config import var
from downloader import CORE_DEVICES_DEVICES, FetchError, Run

SOURCE = "notion"

# The published changelog, and the page id out of its HTML shell.
NOTION_API = "https://ndocs.repebble.com/api/v3/loadPageChunk"
NOTION_PAGE = "25efbb55-ea84-801d-a04b-fcf73c9346e1"

NOTION_RELEASE_ROOT = "https://github.com/coredevices/PebbleOS/releases/download"

# Keeps this track's blob off the canonical one's R2 key when both tracks publish the
# same version for the same hardware.
FILENAME_PREFIX = "github-"

# Notion's own Cloudflare turns away the default runtime user agent, the same
# way it turns away python-urllib.
USER_AGENT = "rebble-cohorts/1.0 (+https://cohorts.rebble.io)"

# A version tag inside a changelog cell like "PebbleOS v4.36.0". This is also
# what keeps the constructed URL safe: whatever the page says, only characters
# matching this reach the release URL, so a cell cannot inject a path or a
# host. "v4.19.1/2" style double releases yield the first of the pair.
VERSION = re.compile(r"v\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?")


def _unwrap(entry):
    """The block itself, out of loadPageChunk's nested {"value": {"value": ...}}."""
    value = entry.get("value") if isinstance(entry, dict) else None
    while isinstance(value, dict) and "value" in value and "type" not in value:
        value = value["value"]
    return value if isinstance(value, dict) else None


def _text(props, key):
    """One rich text property as plain text.

    Notion stores it as a list of [content, [marks...]] runs. Only the content
    is wanted here: the notes column carries its bullets as newlines inside the
    runs, which is the shape the rest of our notes already have.
    """
    out = []
    for run in (props or {}).get(key) or []:
        if run and isinstance(run[0], str):
            out.append(run[0])
    return "".join(out)


def _first_table(blocks, page_id):
    """The id of the first table on the page, in document order."""
    stack = list(reversed((blocks.get(page_id) or {}).get("content") or []))
    while stack:
        block_id = stack.pop()
        block = blocks.get(block_id)
        if block is None:
            continue
        if block.get("type") == "table":
            return block_id
        # Columns hold their own children, so a table can be nested one deep.
        if block.get("type") in ("column_list", "column"):
            stack.extend(reversed(block.get("content") or []))
    return None


def _newest(blocks, table_id):
    """(version, notes) from the table's top data row, or None.

    The version cell is found by pattern rather than by position, and the notes
    are whatever sits in the next column along, which is what the page has done
    since it was a list of core builds.
    """
    table = blocks.get(table_id) or {}
    fmt = table.get("format") or {}
    order = fmt.get("table_block_column_order") or []
    rows = [r for r in (table.get("content") or []) if r in blocks]
    if fmt.get("table_block_column_header"):
        rows = rows[1:]
    if not rows or not order:
        return None

    cells = [_text(blocks[rows[0]].get("properties"), column) for column in order]
    for index, cell in enumerate(cells):
        found = VERSION.search(cell)
        if found:
            notes = cells[index + 1] if index + 1 < len(cells) else ""
            return found.group(0), (notes.strip() or None)
    return None


async def _load_page(api, page_id):
    body = json.dumps(
        {
            "pageId": page_id,
            "limit": 200,
            "cursor": {"stack": []},
            "chunkNumber": 0,
            "verticalColumns": False,
        }
    )
    resp = await fetch(
        api,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        body=body,
    )
    if resp.status >= 400:
        raise FetchError(resp.status)
    raw = ((await resp.json()) or {}).get("recordMap", {}).get("block", {})
    blocks = {}
    for block_id, entry in raw.items():
        block = _unwrap(entry)
        if block:
            blocks[block_id] = block
    return blocks


async def fetch_firmware(env):
    api = var(env, "NOTION_API", NOTION_API)
    page = var(env, "NOTION_PAGE", NOTION_PAGE)
    root = var(env, "NOTION_RELEASE_ROOT", NOTION_RELEASE_ROOT)

    try:
        blocks = await _load_page(api, page)
    except FetchError as e:
        raise RuntimeError(f"could not read the changelog ({e.status})") from None

    table = _first_table(blocks, page)
    entry = _newest(blocks, table) if table else None
    if entry is None:
        # Better to stop than to guess: everything downstream is built out of
        # the version this returns.
        raise RuntimeError("no release row found in the changelog table")

    version, notes = entry
    print(f"changelog: newest release is {version}")

    run = Run(env, source=SOURCE, filename_prefix=FILENAME_PREFIX, url_guessed=True)
    for hardware in CORE_DEVICES_DEVICES:
        url = f"{root}/{version}/normal_{hardware}_{version}.pbz"
        await run.publish(hardware, version, notes, url)

    return run.summary()
