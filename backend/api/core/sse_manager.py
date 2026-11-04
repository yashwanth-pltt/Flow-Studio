"""In-process SSE pub/sub for generation progress.

Channels are keyed on (organization_id, identifier) so two orgs cannot read
each other's stream even if identifiers collide. For multi-worker deployments
this manager should be replaced with a platform-level event stream.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from datetime import datetime
from typing import Any, Dict, Tuple

from fastapi.responses import StreamingResponse

logger = logging.getLogger("pltt_video_studio_2.sse")

# Propagates the user id from the request handler into background tasks
# spawned via asyncio.create_task (which inherits the current context). All
# broadcast_event calls fan out to this user-keyed channel so the front end's
# /events/<user_id> SSE subscription receives every update.
current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pltt_video_studio_2_current_user_id", default=None
)


def bind_user_id(user_id: str) -> None:
    """Call once at the top of every route handler that emits SSE events."""
    if user_id:
        current_user_id.set(user_id)


class StatusManager:
    def __init__(self):
        self.queues: Dict[Tuple[str, str], asyncio.Queue] = {}

    def get_queue(self, org_id: str, identifier: str) -> asyncio.Queue:
        key = (org_id, identifier)
        if key not in self.queues:
            self.queues[key] = asyncio.Queue()
        return self.queues[key]

    async def push_event(self, org_id: str, identifier: str, event_type: str, data: Any):
        logger.info(f"📤 [SSE] '{event_type}' → org={org_id} id={identifier}")
        await self.get_queue(org_id, identifier).put({"type": event_type, "data": data})

    def push_event_threadsafe(self, loop, org_id: str, identifier: str, event_type: str, data: Any):
        key = (org_id, identifier)
        if key not in self.queues:
            self.queues[key] = asyncio.Queue()
        loop.call_soon_threadsafe(self.queues[key].put_nowait, {"type": event_type, "data": data})


status_manager = StatusManager()


async def broadcast_event(org_id: str, event_type: str, data: Any, *identifiers: str) -> None:
    seen = set()
    all_ids = list(identifiers)
    uid = current_user_id.get()
    if uid:
        all_ids.append(uid)
    for identifier in all_ids:
        if identifier and identifier not in seen:
            seen.add(identifier)
            await status_manager.push_event(org_id, identifier, event_type, data)


def broadcast_event_threadsafe(loop, org_id: str, event_type: str, data: Any, *identifiers: str) -> None:
    seen = set()
    for identifier in identifiers:
        if identifier and identifier not in seen:
            seen.add(identifier)
            status_manager.push_event_threadsafe(loop, org_id, identifier, event_type, data)


def _json_safe(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    return obj


def format_generation_item(doc: dict, status: str) -> dict:
    all_urls = doc.get("all_generated_urls") or []
    url = doc.get("url") or (all_urls[0].get("url") if all_urls else None)
    result = {**doc, "status": status, "url": url}
    if "error_message" in result:
        result["error"] = result.pop("error_message")
    return result


async def _event_generator(org_id: str, identifier: str, generation_type: str = "image"):
    logger.info(f"📡 SSE connected org={org_id} id={identifier} type={generation_type}")
    queue = status_manager.get_queue(org_id, identifier)
    event_name = "image_generation" if generation_type == "image" else "video_generation"

    yield "retry: 3000\n\n"
    yield f"event: {event_name}\ndata: {json.dumps({'id': identifier, 'status': 'connected'})}\n\n"

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue

            etype = event.get("type")
            edata = event.get("data") if isinstance(event.get("data"), dict) else {}
            pid = edata.get("id") or edata.get("project_id") or identifier

            if etype == "processing":
                payload = {
                    "id": pid,
                    "project_id": edata.get("project_id"),
                    "generation_id": edata.get("generation_id"),
                    "key_frame_id": edata.get("key_frame_id"),
                    "prompt": edata.get("prompt"),
                    "aspect_ratio": edata.get("aspect_ratio"),
                    "resolution": edata.get("resolution"),
                    "duration": edata.get("duration"),
                    "status": "in_progress",
                    "logs": edata.get("logs", []),
                    "position": edata.get("position"),
                }
            else:
                payload = format_generation_item({**edata, "id": pid}, etype)
                for k in ("project_id", "generation_id", "key_frame_id", "prompt", "aspect_ratio", "resolution", "duration"):
                    payload.setdefault(k, edata.get(k))

            try:
                yield f"event: {event_name}\ndata: {json.dumps(_json_safe(payload))}\n\n"
            except Exception as e:
                logger.error(f"❌ SSE serialization: {e}")
                yield f"event: {event_name}\ndata: {json.dumps({'id': pid, 'status': 'failed', 'error': 'serialization'})}\n\n"
    except asyncio.CancelledError:
        logger.info(f"📡 SSE disconnected org={org_id} id={identifier}")


def get_sse_response(org_id: str, identifier: str, generation_type: str = "image") -> StreamingResponse:
    return StreamingResponse(
        _event_generator(org_id, identifier, generation_type),
        media_type="text/event-stream",
    )
