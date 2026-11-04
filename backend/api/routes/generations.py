"""`GET /generations/{generation_id}` — poll one generation batch.

The browser listens on SSE (`/events/{user_id}`) and uses this as the
reliability net: after a reload, or when an SSE event is missed, it looks up
each pending generation and applies the finished assets to its scenes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends

from palette_sdk import PluginContext, PluginRouter, require_permission

from backend.api.core.org_db import get_ctx
from backend.api.core.sse_manager import _json_safe
from backend.api.db_helpers import get_items_by_generation_id, list_assets_by_item, update_item_failed

router = PluginRouter(tags=["generations"])

PENDING = {"started", "in_progress", "processing"}

# Jobs run as in-process asyncio tasks, so a server restart (or a crashed
# task) leaves their items pending forever and the browser would poll them
# forever. Past the longest a job of that type can really take, treat a
# pending item as interrupted and fail it.
STALE_AFTER = {
    "image": timedelta(minutes=20),       # provider timeout 300s, Midjourney 600s, plus storage
    "story_plan": timedelta(minutes=15),  # reference analysis + 180s LLM calls
    "audio": timedelta(minutes=15),
}
STALE_DEFAULT = timedelta(minutes=60)     # video, camera moves, exports
INTERRUPTED = "Generation was interrupted (the server restarted or the job timed out). Please try again."


def _as_utc(value) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    # SQLite hands back naive UTC timestamps.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _is_stale(item: dict, now: datetime) -> bool:
    started = _as_utc(item.get("updated_at")) or _as_utc(item.get("created_at"))
    return bool(started) and now - started > STALE_AFTER.get(item.get("type") or "", STALE_DEFAULT)


@router.get("/generations/{generation_id}", dependencies=[require_permission("resources:read")])
async def get_generation(generation_id: str, ctx: PluginContext = Depends(get_ctx)):
    items = await get_items_by_generation_id(ctx.db, ctx.user_id, generation_id)
    now = datetime.now(timezone.utc)
    for i, item in enumerate(items):
        if item["status"] in PENDING and _is_stale(item, now):
            items[i] = await update_item_failed(ctx.db, item["id"], INTERRUPTED) or {**item, "status": "failed", "error_message": INTERRUPTED}
    assets = await list_assets_by_item(ctx.db, ctx.user_id, [i["id"] for i in items])
    if not items:
        status = "missing"
    elif any(i["status"] in PENDING for i in items):
        status = "pending"
    elif all(i["status"] == "failed" for i in items):
        status = "failed"
    else:
        status = "completed"
    return _json_safe({"generation_id": generation_id, "status": status, "items": items, "assets": assets})
