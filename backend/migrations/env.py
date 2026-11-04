"""Alembic environment for the Pltt Creative Video plugin.

The Palette platform provides everything Alembic needs through env vars
(`PALETTE_PLUGIN_ID`, `PALETTE_DB_URL`, optional `PALETTE_PLUGIN_ROLE`).
Per the SDK contract this file is intentionally a thin shim so the runner
in `palette_sdk.db.alembic_env` can apply the right schema, role, and
search_path before invoking the migrations.
"""

from palette_sdk.db.alembic_env import run_migrations

run_migrations()
