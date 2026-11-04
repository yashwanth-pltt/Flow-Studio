"""Robust plugin-secret reader.

Resolution order (important — see the `ctx.secret` caveat below):

1. ``ctx.config["secrets"][name]`` — plugin secrets the platform injects into
   the request config (hosted installs, and `pltt dev`).
2. ``os.environ[name]`` — the process environment. This platform injects the
   deployment's ``.env`` values here, so for declared keys this is where
   FAL_KEY/RUNWARE_KEY/… actually live at runtime.
3. ``ctx.secret(name)`` — the documented SDK resolver, used last.

Why `os.environ` comes *before* `ctx.secret`:
The SDK's ``ctx.secret(key)`` only falls back to ``os.environ`` for *undeclared*
keys. For a key declared in `palette-plugin.json` with ``required: true`` (our
FAL_KEY, RUNWARE_KEY, …), if it is absent from ``config["secrets"]`` it hits the
``secret_specs`` branch and **raises ``MissingSecretError`` instead of reading
the environment**. So calling ``ctx.secret`` first would never see a value that
the platform put in ``.env``/``os.environ``. Reading the environment first (and
swallowing the raise as a last resort) makes the documented "secrets are read
from .env" behaviour actually work for declared keys.

Always returns a stripped string so callers can treat "" as "not configured".
"""

from __future__ import annotations

import os
from typing import Any

from palette_sdk import MissingSecretError


def read_secret(ctx: Any, name: str, default: str = "") -> str:
    """Return the plugin secret ``name`` as a stripped string ("" if unset)."""
    # 1) Platform-injected plugin secrets.
    config = getattr(ctx, "config", None)
    if isinstance(config, dict):
        secrets = config.get("secrets")
        if isinstance(secrets, dict):
            value = secrets.get(name)
            if value:
                return str(value).strip()

    # 2) Process environment (this deployment injects `.env` here). Done before
    #    `ctx.secret` because that raises for declared-required keys and would
    #    never reach the environment for FAL_KEY/RUNWARE_KEY/etc.
    env_value = os.environ.get(name)
    if env_value:
        return env_value.strip()

    # 3) SDK resolver last (covers `pltt dev` and any config-only setups).
    try:
        value = ctx.secret(name)
        if value:
            return str(value).strip()
    except MissingSecretError:
        pass

    return (default or "").strip()
