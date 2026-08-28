"""Worker vars and secrets.

These arrive on the binding object rather than in os.environ, so they are read
per request from `env` instead of once at import time. Defaults match the
values wrangler.jsonc declares, so a missing var behaves the same locally and
in production.
"""

DEFAULTS = {
    "FIRMWARE_ROOT": "https://cohorts-storage.lavate.ch/fw",
    "R2_PREFIX": "fw/",
    "DEVICE_SERIAL": "REBBLE_COHORTS_CRON",
    # A Firebase Web API key is a public client identifier, not a secret, so it
    # lives with the vars. This one is CoreApp's, for project coreapp-ce061.
    "CORE_DASH_FIREBASE_KEY": "AIzaSyD-xpPFNkplPF-_DOS1jFCMILg_7yPwHqA",
}


def var(env, name, default=None):
    """Read a var/secret off the binding, treating unset and empty as absent."""
    value = getattr(env, name, None)
    if value is None or value == "":
        return DEFAULTS.get(name, default)
    return value
