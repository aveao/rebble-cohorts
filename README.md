# rebble-cohorts

cohorts.rebble.io: The Rebble cohorts API. It handles firmware delivery.

Runs as a Cloudflare Python Worker: FastAPI behind the runtime's ASGI adapter,
D1 for firmware metadata, R2 for the firmware blobs, and a cron trigger for the
CoreDevice firmware poll.

Firmware is served in two shapes. `/cohort` is the shape classic Pebbles ask
for; `/api/ota/latest` is the shape Core Devices' own dash serves, for CoreApp
and anything else built against it. Both read the same rows.

For archival, use `/cohort?select=fw-all`, which returns a list of all stored firmware.

## Configuration

Vars live in `wrangler.jsonc`; secrets are set with `npx wrangler secret put <NAME>`.
For local development, copy `.dev.vars.example` to `.dev.vars`.

| Name | Kind | Default | Purpose |
| --- | --- | --- | --- |
| `DB` | D1 binding | none | Firmware metadata database |
| `BINARIES` | R2 binding | none | Bucket holding the `.pbz` blobs |
| `FIRMWARE_ROOT` | var | `https://cohorts-storage.lavate.ch/fw` | Public base URL recorded in firmware rows; must resolve to the R2 bucket's custom domain |
| `R2_PREFIX` | var | `fw/` | Key prefix inside the bucket (must line up with the tail of `FIRMWARE_ROOT`) |
| `DEVICE_SERIAL` | var | `REBBLE_COHORTS_CRON` | Serial the cron reports upstream; the Core Devices dash answers per device, so it picks which build we are offered |
| `CORE_DASH_REFRESH_TOKEN` | secret | none | Refresh token for an anonymous Firebase account, required by the cron |
| `CORE_DASH_FIREBASE_KEY` | var | CoreApp's key | Firebase Web API key for project `coreapp-ce061`, a public client identifier rather than a secret |
| `CORE_DASH_API` | var | eng-dash's `/ota/latest` | Override only to point the cron at a stand-in while developing |
| `MEMFAULT_TOKEN` | secret | none | Memfault project key, needed only by the fallback poller |
| `MEMFAULT_API` | var | Memfault's public API | Override only to point that poller at a stand-in while developing |
| `NOTION_API` | var | the changelog's `loadPageChunk` | Override only to point the notion channel at a stand-in while developing |
| `NOTION_PAGE` | var | the changelog page id | Override to read a different Notion page |
| `NOTION_RELEASE_ROOT` | var | PebbleOS release downloads | Override to fetch that channel's blobs from somewhere else |

## Local development

Requires uv >= 0.12.3 (pywrangler enforces this) and Node for `wrangler`.

```
uv sync
uv run pywrangler dev                                    # http://localhost:8787
npx wrangler d1 migrations apply cohorts --local         # create the schema
uv run tools/cli.py import_json > seed.sql               # seed from config.json
npx wrangler d1 execute cohorts --local --file=seed.sql
```

D1 and R2 are emulated locally and persist under `.wrangler/`. Cron triggers do
not fire on a schedule locally; run one by hand:

```
curl http://localhost:8787/cdn-cgi/handler/scheduled
```

## Deploying

```
npx wrangler r2 bucket create cohorts
uv run pywrangler deploy                           # provisions the D1 database
npx wrangler d1 migrations apply cohorts --remote
npx wrangler secret put CORE_DASH_REFRESH_TOKEN
```

The D1 binding carries no `database_id`, so the first deploy creates the
database and writes its id back into `wrangler.jsonc`; the binding stays linked
on later deploys regardless. The R2 bucket is named explicitly instead, because
it is public-facing, dropping `bucket_name` would work the same way but leave
you with a generated name.

Attach a custom domain to the bucket matching `FIRMWARE_ROOT`, so the URLs
recorded in the database resolve to the blobs the cron uploads.

### Deploying from the Cloudflare dashboard

Workers Builds ships Node and Python but no uv, which pywrangler requires, so
both commands have to be set, the default deploy command does not work here.
Plain `npx wrangler deploy` succeeds but uploads only the source files
without the vendored dependencies, producing a Worker that fails at runtime.

| Setting | Value |
| --- | --- |
| Build command | `pip install uv && uv sync` |
| Deploy command | `uv run pywrangler deploy` |
| Non-production branch deploy command | `uv run pywrangler versions upload` |

Migrations are not part of a deploy. Either apply them from your machine, or
chain them ahead of the deploy: `uv run pywrangler d1 migrations apply cohorts
--remote && uv run pywrangler deploy`, the tracking table makes it a no-op when
there is nothing new. Secrets are not build variables: set
`CORE_DASH_REFRESH_TOKEN` with `wrangler secret put` or in the dashboard. On
dashboard deploys, a provisioned D1 id is visible in the dashboard rather than
written back to the repository.

## Firmware data

Firmware rows live in the `firmwares` table, keyed by `(hardware, kind, version)`. Multiple versions per `(hardware, kind)` are allowed so rollback works by submitting an older version with a newer timestamp, `/cohort?select=fw` always returns the latest row per requested kind by `timestamp` descending.

By default `/cohort?select=fw&hardware=<hw>` returns only `normal`. Pass `&includeRecovery=true` to additionally include the latest `recovery` row; only the literal string `true` is recognized, anything else (including absent) is treated as false. If none of the requested kinds yield a row, `/cohort` responds 400.

A row's `size` is the `.pbz`'s length in bytes. The poller records it; rows
that predate the column, and anything submitted without `--size`, leave it
NULL, and `/api/ota/latest` reports 0 for those.

### The eng-dash shape

`GET /api/ota/latest` answers in the shape Core Devices' dash serves, so a
client built against `dash.repebble.com/api` can be pointed at us instead.
`/ota/latest` is registered too, since the client appends `/ota/latest` to a
base URL that may or may not carry the `/api`. `src/ota.py` builds the body.

```
/api/ota/latest?hardware_version=asterix&device_serial=<serial>&current_version=v4.31.1
```

| Param | | |
| --- | --- | --- |
| `hardware_version` | required | The `hardware` column, as on `/cohort` |
| `device_serial` | accepted, ignored | See below |
| `current_version` | optional | Omitted by a watch in recovery |
| `source` | optional | Ours, not eng-dash's: picks a track as on `/cohort` |

**No auth.** The real endpoint wants a Firebase ID token, but the app signs in
anonymously whenever nobody is logged in, so it separates nobody from nobody.
We have nothing to authorise against and no account to resolve a track from, so
an `Authorization` header is ignored rather than rejected. `device_serial` is
accepted and ignored for the same reason, and unlike upstream it is not
required: refusing a request over a field we never read would only turn into an
`UpdateCheckFailed` and a fallback.

The two responses:

- **204** when there is nothing to offer, which covers both having no `normal`
  row for the hardware and the watch already running the one we have. This
  matters more than it looks: on a 200 the client does not compare versions, it
  installs what it is handed, so "already current" has to be a 204 rather than
  the version it is already on. Unknown hardware is a 204 too, not a 400, since
  anything that is not 200 or 204 reads as `UpdateCheckFailed`.
- **200** with the newest `normal` row otherwise. `is_downgrade` is worked out
  by comparing what we are about to offer against `current_version`, because
  the client refuses an older build unless the response says it may take one.
  Rollback in cohorts is exactly that, an older version published with a newer
  timestamp, so without the flag a rollback would be offered and then refused.

Every field the real endpoint sends is sent, including the ones the client
documents as ignoring, since it deserialises the whole object and a field
missing here is a field its parser may still insist on. `must_pass_through` is
always false: we have no notion of a release that must be installed on the way
past.

### Sources

A row's `source` names the build track it came from. `NULL` is the canonical
firmware and is what a request without `&source=` gets; anything else is a
parallel track a client opts into:

```
/cohort?select=fw&hardware=snowy_dvt                  # canonical
/cohort?select=fw&hardware=snowy_dvt&source=notion    # the notion track
/cohort?select=fw-all&source=coredevices-github       # everything on that track
```

Tracks are independent: `latest` is resolved per source, so a track publishing
an older version does not affect the canonical one, and an unknown source has no
rows and therefore 400s exactly as an unknown hardware does. `source` is
included in the `fw-all` archival rows; the `fw` payload is unchanged, so
watches see the same shape as before. Cached responses key off the full URL, so
each source caches separately.

Two sources may publish the same version for the same hardware, identity is
`(hardware, kind, version, source)`, enforced by a unique index over
`COALESCE(source, '')` because SQLite treats NULLs as distinct.

The cron writes two tracks: core-dash publishes the canonical rows, and the
notion channel publishes `notion`. Any other track is populated by hand with
`tools/cli.py submit_firmware --source`.

### Admin commands

A Worker has no CLI, so the management commands run locally and emit SQL you
feed to wrangler. Drop `--remote` to apply against the local dev database.

Seeding from `config.json` (retained only as seed data for the initial import),
which is idempotent, rows are upserted by `(hardware, kind, version)`:

```
uv run tools/cli.py import_json > seed.sql
npx wrangler d1 execute cohorts --remote --file=seed.sql
```

Adding or updating a single firmware. `kind` must be `normal` or `recovery`,
and `--timestamp` defaults to now:

```
uv run tools/cli.py submit_firmware <hardware> <kind> <version> <url> <sha256> \
    [--timestamp <unix>] [--notes "<text>"] [--size <bytes>] > fw.sql
npx wrangler d1 execute cohorts --remote --file=fw.sql
```

Both build URLs from `--firmware-root` (default `https://cohorts-storage.lavate.ch/fw`),
so make sure it matches the Worker's `FIRMWARE_ROOT`. URLs are formed on insert,
not on request.

### Fetching CoreDevice firmware

`src/downloader.py` holds everything the pollers share. For a device it skips
the offered version if it is already recorded, and otherwise downloads the
`.pbz` while hashing it, uploads it to R2 at
`{R2_PREFIX}{hardware}/Pebble-{version}-{hardware}.pbz`, and upserts a `normal`
row with the resulting `{FIRMWARE_ROOT}/…` URL and the computed sha256. All a
poller does is work out what the newest version for a hardware is.

The upload is skipped when we already hold the bytes. Before storing anything,
every channel looks for a row whose `sha256` and `size` both match what it just
downloaded, and points the new row at that object instead. A version that shows
up on the notion track and later on the canonical one is one file downloaded
twice, and this keeps it as one object in R2. The download still happens: the
digest is not known until it does.

Two consequences worth knowing. A row can point at a blob whose filename
carries another track's prefix, so a canonical row may serve a `github-` URL,
which `/api/ota/latest` then reports as its `filename`. And rows from before
the `size` column are never reuse candidates: their length is unknown, so they
are treated as a mismatch and the blob is uploaded again.

There are two, and they differ only in who they ask:

| | `src/core_dash.py` | `src/memfault.py` |
| --- | --- | --- |
| Upstream | `dash.repebble.com/api/ota/latest` | `api.memfault.com/api/v0/releases/latest` |
| Auth | anonymous Firebase ID token | project key |
| On the cron | yes | no, kept as a fallback |

`core-dash` is the endpoint CoreApp itself asks first, so it is what the cron
runs for the canonical track. Memfault is the app's own fallback and stays
wired up in the same sense: swapping it into `CHANNELS` in `src/entry.py` is
the whole change.

#### The notion channel

`src/notion.py` publishes the `notion` track from the PebbleOS GitHub
releases, which carry a build for days before it reaches the OTA track our
account resolves to. The track is named for where the version comes from
rather than for how stable it is: these are the published releases, they just
arrive on GitHub first. It reads the top row of the table on the [PebbleOS
changelog](https://ndocs.repebble.com/pebbleos-changelog), takes the version
out of it, and tries one release asset per hardware:

```
https://github.com/coredevices/PebbleOS/releases/download/<version>/normal_<hardware>_<version>.pbz
```

The changelog is a Notion page, and needs no token: a published page answers
`loadPageChunk` unauthenticated, which is the call the site's own frontend
makes. It does need a plausible `User-Agent`, since Notion's Cloudflare turns
away the default runtime one.

The version cell is found by matching `v<major>.<minor>[.<patch>][-suffix]`
rather than by column position, and the notes are taken from the next column
along. That regex is also the safety boundary: whatever the page says, only
characters matching it reach the constructed URL.

Because the asset URL is constructed rather than advertised, a hardware with no
build in a release is an ordinary 404 rather than a fault, and the run counts
those as "not published" rather than failed. Only `normal` is fetched, though
the releases also carry `recovery_` and per-slot assets.

Its blobs get a `github-` prefix on their filename, naming where the bytes
came from, so the same version on both tracks cannot land on one R2 key. Everything else, hashing, upload and
upsert, is the shared path in `src/downloader.py`.

The two channels are independent: `scheduled` runs each in turn and catches
whatever the other raises, so an expired dash token cannot cost the notion run
and a changelog that will not parse cannot cost the canonical one.

One hourly cron polls every device on both channels in a single invocation. Hashing is the only
part that costs meaningful CPU, and it only happens for a version that is
actually new, so a run where nothing has been published does almost no work.

Keep the interval at an hour or longer: Cloudflare caps cron invocations at 30s
CPU below an hourly interval, versus 15 minutes at an hour or above.

Trigger a run locally:

```
curl http://localhost:8787/cdn-cgi/handler/scheduled
```

#### The anonymous Firebase credential

eng-dash requires a Firebase ID token for project `coreapp-ce061` and accepts an
anonymous one, which is why the endpoint is often described as needing no auth:
CoreApp signs in anonymously whenever nobody is logged in, so every install
carries a valid token.

ID tokens last an hour, the same as the cron interval, so there is nothing worth
caching between runs. What we store is the refresh token of a single anonymous
account, minted once by hand; every run exchanges it at
`securetoken.googleapis.com` for a fresh ID token. Refresh tokens do not expire,
and a Worker cannot write to its own env, so the rotated one Firebase hands back
is ignored.

Mint the account once and take `refreshToken` from the response:

```
curl -sS -X POST \
  "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=$CORE_DASH_FIREBASE_KEY" \
  -H 'Content-Type: application/json' -d '{"returnSecureToken":true}'
npx wrangler secret put CORE_DASH_REFRESH_TOKEN
```

A failure to refresh raises rather than being counted per device: without a
token every lookup 401s, so there is no point reporting it six times. Note that
the release eng-dash serves is chosen by the account's track together with
`DEVICE_SERIAL`, so a different account or serial can be offered a different
build.

### Migrations

`migrations/` holds plain SQL applied by `wrangler d1 migrations apply`, which
tracks what it has run in a `d1_migrations` table. Add a new one with:

```
npx wrangler d1 migrations create cohorts "<message>"
```

## Landing page

`public/index.html` lists the current build for each watch. It is served from
Workers static assets, which are matched ahead of the Worker, so viewing it
costs no invocation; paths with no matching file fall through to the API as
before.

The page holds its own catalogue of the hardware it lists, with the marketing
name for each codename and the validation stage (EVT, DVT, PVT) where the
hardware is pre-production. It asks `/cohort?select=fw&hardware=<name>` once per
device, exactly the request a watch makes, and omits `&source=` entirely for
devices on the canonical build so those requests share the cache entries watches
have already warmed. Hardware with no row renders as "No build" rather than
disappearing.

"Core Devices (Notion)" lists the same three watches again on the `notion`
track, so
a watch can appear in more than one group. Results are therefore keyed by
hardware *and* track; keying on hardware alone would have the two groups render
each other's build.

Adding hardware, or pointing a device at a build track, means editing `GROUPS`
in that file.

## Caching

`/cohort` responses carry `Cache-Control: public, max-age=0, s-maxage=300`, and
`src/entry.py` checks `caches.default` before handing the request to the ASGI
app. Workers run in front of the cache, so a hit still costs an invocation, but
it skips FastAPI, D1 and serialisation, locally that halves the request.

`s-maxage` scopes the caching to the edge; `max-age=0` means clients do not hold
a copy, so watches keep asking and only Cloudflare has one. Only 200s with that
header are stored, which leaves `/heartbeat` and the 400s uncached. Change the
window with `CACHE_CONTROL` in `src/api.py`: it bounds how long a firmware the
cron has just published can sit unseen behind a cached response.

## Notes on the runtime

- Outbound HTTP must go through the runtime's `fetch` (`from workers import fetch`).
  `httpx` is nominally supported but falls back to raw TCP sockets, which the
  Workers runtime does not provide, and fails with `NotImplementedError`.
- The `scheduled(self, controller, env, ctx)` handler receives `env` as `None`;
  the bindings are on `self.env`.
- `requires-python` in pyproject.toml governs the local tooling venv only, and
  is deliberately wide so uv can use whatever CPython is already installed.
  pywrangler resolves the Worker's own interpreter and dependencies into
  `pylock.toml`, and that one is always downloaded: it is a Pyodide
  `emscripten-wasm32` build (~14 MB), not something a system Python can stand in
  for, so `UV_PYTHON_DOWNLOADS=never` breaks the build.
