# rebble-cohorts

cohorts.rebble.io: The Rebble cohorts API. It handles firmware delivery.

Runs as a Cloudflare Python Worker: FastAPI behind the runtime's ASGI adapter,
D1 for firmware metadata, R2 for the firmware blobs, and a cron trigger for the
Memfault poll.

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
| `MEMFAULT_TOKEN` | secret | none | Memfault project key, required by the cron |
| `MEMFAULT_API` | var | Memfault's public API | Override only to point the cron at a stand-in while developing |

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
npx wrangler secret put MEMFAULT_TOKEN
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
Plain `npx wrangler deploy` succeeds but uploads only the five source files
without the vendored dependencies, producing a Worker that fails at runtime.

| Setting | Value |
| --- | --- |
| Build command | `pip install uv && uv sync` |
| Deploy command | `uv run pywrangler deploy` |
| Non-production branch deploy command | `uv run pywrangler versions upload` |

Migrations are not part of a deploy. Either apply them from your machine, or
chain them ahead of the deploy: `uv run pywrangler d1 migrations apply cohorts
--remote && uv run pywrangler deploy`, the tracking table makes it a no-op when
there is nothing new. Secrets are not build variables: set `MEMFAULT_TOKEN` with
`wrangler secret put` or in the dashboard. On dashboard deploys, a provisioned
D1 id is visible in the dashboard rather than written back to the repository.

## Firmware data

Firmware rows live in the `firmwares` table, keyed by `(hardware, kind, version)`. Multiple versions per `(hardware, kind)` are allowed so rollback works by submitting an older version with a newer timestamp, `/cohort?select=fw` always returns the latest row per requested kind by `timestamp` descending.

By default `/cohort?select=fw&hardware=<hw>` returns only `normal`. Pass `&includeRecovery=true` to additionally include the latest `recovery` row; only the literal string `true` is recognized, anything else (including absent) is treated as false. If none of the requested kinds yield a row, `/cohort` responds 400.

### Sources

A row's `source` names the build track it came from. `NULL` is the canonical
firmware and is what a request without `&source=` gets; anything else is a
parallel track a client opts into:

```
/cohort?select=fw&hardware=snowy_dvt                  # canonical
/cohort?select=fw&hardware=snowy_dvt&source=beta      # the beta track
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

Nothing writes a non-NULL source yet: the Memfault cron still publishes
canonical rows, and other tracks are populated by hand with
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
    [--timestamp <unix>] [--notes "<text>"] > fw.sql
npx wrangler d1 execute cohorts --remote --file=fw.sql
```

Both build URLs from `--firmware-root` (default `https://cohorts-storage.lavate.ch/fw`),
so make sure it matches the Worker's `FIRMWARE_ROOT`. URLs are formed on insert,
not on request.

### Fetching CoreDevice firmware from Memfault

`src/memfault.py` holds the logic. For a device it checks Memfault's
`releases/latest`, skips the version if already recorded, and otherwise
downloads the `.pbz` while hashing it, uploads it to R2 at
`{R2_PREFIX}{hardware}/Pebble-{version}-{hardware}.pbz`, and upserts a `normal`
row with the resulting `{FIRMWARE_ROOT}/…` URL and the computed sha256.

One hourly cron polls every device in a single invocation. Hashing is the only
part that costs meaningful CPU, and it only happens for a version that is
actually new, so a run where nothing has been published does almost no work.

Keep the interval at an hour or longer: Cloudflare caps cron invocations at 30s
CPU below an hourly interval, versus 15 minutes at an hour or above.

Trigger a run locally:

```
curl http://localhost:8787/cdn-cgi/handler/scheduled
```

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
