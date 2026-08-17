import asgi
from workers import WorkerEntrypoint

from api import app
from memfault import CRON_DEVICES, fetch_firmware


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)

    async def scheduled(self, controller, env, ctx):
        # The `env` argument arrives as None in Python Workers; the bindings are
        # on self.env. Each schedule handles one device, so an invocation only
        # ever hashes one firmware; an unrecognised schedule does the lot, which
        # keeps a stale wrangler.jsonc from silently skipping devices.
        await fetch_firmware(self.env, CRON_DEVICES.get(controller.cron))
