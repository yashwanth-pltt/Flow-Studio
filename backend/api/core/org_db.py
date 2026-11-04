"""Row-level-security context for the plugin's request DB session.

Every plugin table is created with Palette's org-isolation policy:

    USING      (organization_id = current_setting('app.current_org_id', true)::bigint)
    WITH CHECK (organization_id = current_setting('app.current_org_id', true)::bigint)

When that session variable is missing, `current_setting(..., true)` returns
NULL, the comparison is NULL, and Postgres treats the policy as failed. Reads
then return zero rows *silently* and writes blow up with

    InsufficientPrivilegeError: new row violates row-level security policy
    for table "pltt_video_studio_2__projects"

The platform applies the variable to the connection backing `ctx.db` at the
start of a request, but that guarantee does not survive this plugin's usage
pattern: the CRUD helpers in `db_helpers.py` `commit()` after almost every
write, and a committed SQLAlchemy session releases its connection back to the
pool. The next statement checks out a *different* pooled connection, which
never had the variable set — so anything after the first commit in a request
(and anything after a commit the platform itself made before handing the
session over) runs with no org context at all. A connection pooler in front of
Postgres (PgBouncer in transaction mode) breaks a session-level setting the
same way.

The fix is to (re)apply the variable per transaction instead of per request:
`after_begin` fires every time the session starts a transaction on a
connection, whichever pooled connection that turns out to be. It is applied
with `is_local => true`, so it is scoped to that transaction and can never
leak onto a pooled connection that another organisation picks up later.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from palette_sdk import PluginContext, get_plugin_context

logger = logging.getLogger("pltt_video_studio_2.db")

ORG_GUC = "app.current_org_id"

# Attribute stamped on the underlying sync Session so the listener is attached
# at most once per session object. It holds a mutable box with the org id, so a
# reused session can be re-pointed without stacking listeners.
_LISTENER_ATTR = "_pltt_org_guc_listener"

_SET_ORG_SQL = f"SELECT set_config('{ORG_GUC}', :oid, true)"


def _coerce_org_id(organization_id: Any) -> Optional[int]:
    try:
        return int(organization_id)
    except (TypeError, ValueError):
        return None


def _dialect_name(db: AsyncSession) -> str:
    for getter in (lambda: db.get_bind(), lambda: getattr(db, "bind", None)):
        try:
            bind = getter()
        except Exception:
            continue
        dialect = getattr(bind, "dialect", None)
        name = getattr(dialect, "name", None)
        if name:
            return name
    return ""


def install_org_rls_listener(db: AsyncSession, organization_id: Any) -> None:
    """Re-apply `app.current_org_id` on every transaction this session opens.

    Idempotent and cheap: safe to call on every request. No-op on non-Postgres
    binds (local dev/tests may run on SQLite, which has no RLS).
    """
    oid = _coerce_org_id(organization_id)
    if db is None or oid is None:
        return

    sync_session = getattr(db, "sync_session", db)
    box = getattr(sync_session, _LISTENER_ATTR, None)
    if box is not None:
        # Listener already attached to this session — just point it at the
        # current org (the session is per-request, so this is normally a no-op).
        box["oid"] = oid
        return

    box = {"oid": oid}
    setattr(sync_session, _LISTENER_ATTR, box)

    @event.listens_for(sync_session, "after_begin")
    def _apply_org_guc(_session, _transaction, connection):  # noqa: ANN001
        if connection.dialect.name != "postgresql":
            return
        try:
            connection.exec_driver_sql(
                f"SELECT set_config('{ORG_GUC}', '{box['oid']}', true)"
            )
        except Exception:  # pragma: no cover - never break a request over this
            logger.exception("failed to apply %s=%s on new transaction", ORG_GUC, box["oid"])


async def ensure_org_rls_context(db: AsyncSession, organization_id: Any) -> None:
    """Install the per-transaction listener and fix the *current* transaction.

    `after_begin` only fires for transactions started from now on. If the
    platform (or an earlier dependency) already opened one on this session, that
    open transaction still has no org context, so set it explicitly too.
    """
    oid = _coerce_org_id(organization_id)
    if db is None or oid is None:
        return

    install_org_rls_listener(db, oid)

    if _dialect_name(db) != "postgresql":
        return

    try:
        if db.in_transaction():
            await db.execute(text(_SET_ORG_SQL), {"oid": str(oid)})
    except Exception:
        # A failed statement poisons the transaction; roll back so the endpoint
        # starts clean (the listener will set the variable on the next begin).
        logger.exception("failed to apply %s=%s to the open transaction", ORG_GUC, oid)
        try:
            await db.rollback()
        except Exception:
            pass


async def get_ctx(request: Request) -> PluginContext:
    """`get_plugin_context` + a guaranteed RLS org context on `ctx.db`.

    Use this instead of `palette_sdk.get_plugin_context` in every route: without
    it, any query after the first `commit()` in a request can land on a pooled
    connection where `app.current_org_id` was never set.
    """
    ctx = await get_plugin_context(request)
    await ensure_org_rls_context(ctx.db, ctx.organization_id)
    return ctx


__all__ = [
    "ORG_GUC",
    "ensure_org_rls_context",
    "get_ctx",
    "install_org_rls_listener",
]
