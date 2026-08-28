import asgi
from js import caches
from workers import WorkerEntrypoint

import beta
import core_dash
from api import app

# The channels the cron polls, in order. core-dash publishes the canonical
# firmware, the rows a /cohort request without ?source= gets; beta publishes to
# its own track. They share the R2 bucket and the firmwares table, nothing else.
CHANNELS = (("core-dash", core_dash.fetch_firmware), ("beta", beta.fetch_firmware))


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # Workers run in front of the cache, so a hit still costs an invocation.
        # What it saves is everything behind this line: FastAPI, D1 and
        # serialisation, which is most of the per-request work. Only responses
        # that asked to be cached (see Cache-Control in api.py) are stored, so
        # /heartbeat and the 400s stay live.
        cache = caches.default
        key = request.js_object
        cacheable = request.method == "GET"

        if cacheable:
            hit = await cache.match(key)
            if hit is not None:
                return hit

        response = await asgi.fetch(app, request, self.env)
        if cacheable and response.status == 200 and response.headers.get("Cache-Control"):
            self.ctx.waitUntil(cache.put(key, response.clone()))
        return response

    async def scheduled(self, controller, env, ctx):
        # The `env` argument arrives as None in Python Workers; the bindings are
        # on self.env.
        for name, poll in CHANNELS:
            # One channel failing outright, an expired token or a changelog
            # that will not parse, must not cost us the other one's run.
            try:
                await poll(self.env)
            except Exception as e:  # noqa: BLE001 - whatever a channel raises, the next still runs
                print(f"[{name}]: run FAILED ({e})")
