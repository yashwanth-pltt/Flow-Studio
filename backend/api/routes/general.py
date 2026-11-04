"""Status, model catalog, workspace bootstrap and user preferences."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends
from pydantic import BaseModel

from palette_sdk import PluginContext, PluginRouter, require_permission

from backend.api.core import platform_models
from backend.api.core.catalog import catalog
from backend.api.core.org_db import get_ctx
from backend.api.db_helpers import (
    get_preferences,
    list_assets,
    list_projects,
    list_versions,
    set_preferences,
)

router = PluginRouter(tags=["general"])


@router.get("/status", dependencies=[require_permission("resources:read")])
async def status(ctx: PluginContext = Depends(get_ctx)):
    return {
        "status": "ok",
        "plugin_id": ctx.plugin_id,
        "user_id": ctx.user_id,
        "organization_id": ctx.organization_id,
    }


@router.get("/models", dependencies=[require_permission("resources:read")])
async def models(ctx: PluginContext = Depends(get_ctx)):
    """Text, video, image and audio models with an `available` flag per model
    (based on configured provider keys / platform services)."""
    await platform_models.warm(ctx)
    return catalog(ctx)


@router.get("/bootstrap", dependencies=[require_permission("resources:read")])
async def bootstrap(ctx: PluginContext = Depends(get_ctx)):
    """Everything the workspace needs on load, in one round trip."""
    await platform_models.warm(ctx)
    return {
        "user_id": ctx.user_id,
        "projects": await list_projects(ctx.db, ctx.user_id),
        "assets": await list_assets(ctx.db, ctx.user_id, include_trashed=True),
        "versions": await list_versions(ctx.db, ctx.user_id),
        "preferences": await get_preferences(ctx.db, ctx.user_id),
        "capabilities": catalog(ctx),
    }


class PreferencesBody(BaseModel):
    data: Dict[str, Any]


@router.put("/preferences", dependencies=[require_permission("resources:write")])
async def save_preferences(body: PreferencesBody, ctx: PluginContext = Depends(get_ctx)):
    return {
        "preferences": await set_preferences(
            ctx.db, organization_id=ctx.organization_id, user_id=ctx.user_id, data=body.data
        )
    }
