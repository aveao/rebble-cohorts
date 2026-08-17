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
| `DB` | D1 binding | — | Firmware metadata database |
| `BINARIES` | R2 binding | — | Bucket holding the `.pbz` blobs |
| `FIRMWARE_ROOT` | var | `https://cohorts-storage.lavate.ch/fw` | Public base URL recorded in firmware rows; must resolve to the R2 bucket's custom domain |
| `R2_PREFIX` | var | `fw/` | Key prefix inside the bucket (must line up with the tail of `FIRMWARE_ROOT`) |
| `REBBLE_AUTH` | var | empty | Rebble auth service URL; if empty, `Authorization` headers on `/cohort` are ignored |
| `MEMFAULT_TOKEN` | secret | — | Memfault project key, required by the cron |
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
it is public-facing — dropping `bucket_name` would work the same way but leave
you with a generated name.

Attach a custom domain to the bucket matching `FIRMWARE_ROOT`, so the URLs
recorded in the database resolve to the blobs the cron uploads.

### Deploying from the Cloudflare dashboard

Workers Builds ships Node and Python but no uv, which pywrangler requires, so
both commands have to be set — the default deploy command does not work here.
Plain `npx wrangler deploy` succeeds but uploads only the five source files
without the vendored dependencies, producing a Worker that fails at runtime.

| Setting | Value |
| --- | --- |
| Build command | `pip install uv && uv sync` |
| Deploy command | `uv run pywrangler deploy` |
| Non-production branch deploy command | `uv run pywrangler versions upload` |

Migrations are not part of a deploy. Either apply them from your machine, or
chain them ahead of the deploy: `uv run pywrangler d1 migrations apply cohorts
--remote && uv run pywrangler deploy` — the tracking table makes it a no-op when
there is nothing new. Secrets are not build variables: set `MEMFAULT_TOKEN` with
`wrangler secret put` or in the dashboard. On dashboard deploys, a provisioned
D1 id is visible in the dashboard rather than written back to the repository.

## Firmware data

Firmware rows live in the `firmwares` table, keyed by `(hardware, kind, version)`. Multiple versions per `(hardware, kind)` are allowed so rollback works by submitting an older version with a newer timestamp — `/cohort?select=fw` always returns the latest row per requested kind by `timestamp` descending.

By default `/cohort?select=fw&hardware=<hw>` returns only `normal`. Pass `&includeRecovery=true` to additionally include the latest `recovery` row; only the literal string `true` is recognized, anything else (including absent) is treated as false. If none of the requested kinds yield a row, `/cohort` responds 400.

### Admin commands

A Worker has no CLI, so the management commands run locally and emit SQL you
feed to wrangler. Drop `--remote` to apply against the local dev database.

Seeding from `config.json` (retained only as seed data for the initial import),
which is idempotent — rows are upserted by `(hardware, kind, version)`:

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

There is one cron per CoreDevice, staggered ten minutes apart, so an invocation
downloads and hashes at most one firmware. `CRON_DEVICES` in `src/memfault.py`
maps each expression to its device and has to match `triggers.crons` in
`wrangler.jsonc`; an expression missing from the map polls every device rather
than silently skipping any. The schedules are staggered because
`controller.cron` identifies a schedule by its expression, so duplicates would
be indistinguishable.

Keep each expression at an hour or longer: Cloudflare caps cron invocations at
30s CPU below an hourly interval, versus 15 minutes at an hour or above.

Trigger one locally by passing the expression:

```
curl "http://localhost:8787/cdn-cgi/handler/scheduled?cron=0+*+*+*+*"
```

### Migrations

`migrations/` holds plain SQL applied by `wrangler d1 migrations apply`, which
tracks what it has run in a `d1_migrations` table. Add a new one with:

```
npx wrangler d1 migrations create cohorts "<message>"
```

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
