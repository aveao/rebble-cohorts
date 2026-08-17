"""Worker vars and secrets.

These arrive on the binding object rather than in os.environ, so they are read
per request from `env` instead of once at import time. Defaults match the
values wrangler.jsonc declares, so a missing var behaves the same locally and
in production.
"""

DEFAULTS = {
    "FIRMWARE_ROOT": "https://cohorts-storage.ave.zone/fw",
    "R2_PREFIX": "fw/",
}


def var(env, name, default=None):
    """Read a var/secret off the binding, treating unset and empty as absent."""
    value = getattr(env, name, None)
    if value is None or value == "":
        return DEFAULTS.get(name, default)
    return value
