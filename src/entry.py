import asgi
from workers import WorkerEntrypoint

from api import app
from memfault import fetch_firmware


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)

    async def scheduled(self, controller, env, ctx):
        # The `env` argument arrives as None in Python Workers; the bindings are
        # on self.env.
        await fetch_firmware(self.env)
