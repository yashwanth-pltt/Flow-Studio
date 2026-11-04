"""Flow Video 2.0 plugin backend entry.

The Palette platform mounts this `router` at `/api/v1/plugins/pltt-video-studio-2/*`.
No FastAPI app instance, no uvicorn, no CORS — the host runtime handles that.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Put the plugin root on sys.path so every cross-module import below uses
# the fully-qualified `backend.api.X` name. This avoids two failure modes
# the hosted reviewer hit:
#   * generic top-level names (`routes`, `models`, `db_helpers`) collided
#     with the same names from other plugins cached in `sys.modules`, so
#     `from routes import assets_router` resolved to a stale module without
#     `assets_router` and surfaced as "route permission gate check failed".
#   * importing the same model module under two names (`models` vs
#     `backend.api.models`) would register each OrgScopedTable on the
#     metadata twice and explode with "Table already defined".
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
# The CLI bundles the backend SDK at `backend/palette_sdk`, so the `backend`
# directory must also be on sys.path for the bare `from palette_sdk import ...`
# below to resolve. The hosted route loader only puts the plugin root on
# sys.path (which is what makes `from backend.api.X` work), so without this the
# import failed with "No module named 'palette_sdk'" and the whole backend
# route load aborted — leaving the platform serving no/stale backend.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
for _path in (str(_BACKEND_DIR), str(_PLUGIN_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# CRITICAL (shared-process isolation): the hosted runtime keeps modules cached
# in `sys.modules` across plugins. Both this app and the sibling image app
# expose the SAME package path `backend.api.*` (e.g. `backend.api.models`), so a
# plain `import backend.api.models` can return the OTHER app's already-cached
# copy — which defines the image app's `pltt_creative__*` tables and makes every
# query hit a relation that doesn't exist in THIS plugin's schema. Evict any
# cached `backend`/`backend.*` (and `palette_sdk`) modules whose file is NOT
# under this plugin's root, so the imports below load THIS plugin's own files
# (its root was just inserted first on sys.path).
_root_str = str(_PLUGIN_ROOT)
# `backend.*` are this plugin's qualified modules; the bare names are the
# aliases models.py/db_helpers register (and that older revisions used) which
# also collide across plugins.
_GENERIC_ALIASES = ("models", "db_helpers", "routes")
for _name in list(sys.modules):
    _top = _name.split(".", 1)[0]
    if _name == "backend" or _name.startswith("backend.") or _top in _GENERIC_ALIASES:
        _mod = sys.modules.get(_name)
        _file = getattr(_mod, "__file__", None) or ""
        # Only evict a copy that belongs to a DIFFERENT plugin directory; never
        # touch this plugin's own already-loaded modules or file-less builtins.
        if _file and not _file.startswith(_root_str):
            del sys.modules[_name]

import logging

from fastapi import Depends

from palette_sdk import LifecycleHooks, PluginContext, PluginRouter, require_permission

from backend.api.core.org_db import ORG_GUC, get_ctx
from backend.api.models import Project

# Import every router from its submodule directly — never as an attribute of
# the `backend.api.routes` package, which the hosted runtime may hold as a
# stale cached object from another revision.
from backend.api.routes.general import router as general_router
from backend.api.routes.assets import router as assets_router
from backend.api.routes.events import router as events_router
from backend.api.routes.generations import router as generations_router
from backend.api.routes.generate_video import router as generate_video_router
from backend.api.routes.generate_image import router as generate_image_router
from backend.api.routes.story import router as story_router
from backend.api.routes.audio import router as audio_router
from backend.api.routes.projects import router as projects_router

from backend.api.core.std_logs import setup_logging  # standard server logs (stdout only)

setup_logging()

router = PluginRouter(tags=["pltt-video-studio-2"])

# Add each sub-router's routes directly instead of `include_router()`:
# FastAPI >= 0.142 wraps included routers in a lazy `_IncludedRouter`, which
# hides the routes (and their `require_permission` gates) from the platform's
# route inspection. Plain APIRoute objects behave the same on every version.
for _r in (
    general_router, assets_router, events_router, generations_router, generate_video_router,
    generate_image_router, story_router, audio_router, projects_router,
):
    router.routes.extend(_r.routes)


@router.get("/diagnostics/db", dependencies=[require_permission("resources:read")])
async def db_diagnostics(ctx: PluginContext = Depends(get_ctx)):
    """Row-level-security context of this request's DB session.
    `org_guc` / `org_guc_after_commit` must both equal `organization_id`."""
    from sqlalchemy import func, select, text

    info: dict = {"organization_id": ctx.organization_id, "user_id": ctx.user_id}
    try:
        row = (await ctx.db.execute(text(
            f"SELECT current_setting('{ORG_GUC}', true) AS org_guc, "
            "current_setting('search_path') AS search_path, current_user AS db_user"
        ))).mappings().first()
        info.update(dict(row or {}))
        await ctx.db.commit()
        info["org_guc_after_commit"] = (
            await ctx.db.execute(text(f"SELECT current_setting('{ORG_GUC}', true)"))
        ).scalar()
        info["visible_projects"] = (await ctx.db.execute(select(func.count(Project.id)))).scalar()
        await ctx.db.commit()
    except Exception as e:  # pragma: no cover - diagnostics only
        info["error"] = f"{type(e).__name__}: {e}"
    return info


lifecycle = LifecycleHooks()


@lifecycle.on_install
async def on_install(ctx: PluginContext) -> None:
    """Migrations create the tables; nothing to seed."""
    logging.getLogger("pltt_video_studio_2").info("installed for org %s", ctx.organization_id)
