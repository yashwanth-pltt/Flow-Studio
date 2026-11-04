"""SSE generation progress stream.

Auth and org scoping come from the PluginContext; channels are keyed on
(organization_id, project_id) so two orgs cannot read each other's stream.
"""

from __future__ import annotations

from fastapi import Depends

from palette_sdk import (
    PluginContext,
    PluginRouter,
    require_permission,
)

from backend.api.core.org_db import get_ctx

from backend.api.core.sse_manager import get_sse_response

router = PluginRouter(tags=["events"])


@router.get(
    "/events/{user_id}",
    dependencies=[require_permission("resources:read")],
)
async def event_stream(
    user_id: str,
    type: str = "image",
    ctx: PluginContext = Depends(get_ctx),
):
    # Generation events are published per user (the browser subscribes with its user id).
    return get_sse_response(ctx.organization_id, user_id, generation_type=type)
