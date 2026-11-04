"""Video generation: per-scene clips, retry, and camera moves.

One background job per request (`start_video_job`): the platform's media
service first (`ctx.media`, only when its catalog lists the model's fal
endpoint), then Fal on FAL_KEY, Runware as the fallback, then (for camera
moves with "quality check + one retry") the next model in `fallback_models`. A finished clip is copied into app storage
and registered as a Library asset (category Videos) linked to the project,
and a `completed` SSE event carries that asset so the browser can drop it
into the scene.
"""

from __future__ import annotations

import asyncio
import functools
import tempfile
from pathlib import Path
import logging
import uuid
from typing import Any, Dict, List, Optional

import anyio
import fal_client
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from palette_sdk import PluginContext, PluginRouter, require_permission

from backend.api.core.std_logs import call as log_call, mark_failed, run_task
from backend.api.core.assets_service import store_asset_from_url
from backend.api.core.bg_session import standalone_background_session
from backend.api.core.camera_prompt import build_camera_prompt
from backend.api.core.catalog import DEFAULT_VIDEO_MODEL, clamp_duration, fit_aspect_ratio, video_model
from backend.api.core import platform_models
from backend.api.core.fal_helper import build_payload, generate_video, video_request
from backend.api.core.org_db import get_ctx
from backend.api.core.runware_helper import generate_video_runware, get_runware_video_model
from backend.api.core.secrets import read_secret
from backend.api.core.sse_manager import bind_user_id, broadcast_event, broadcast_event_threadsafe
from backend.api.core.storage_helper import fetch_media_bytes, provider_safe_media_url, upload_bytes_to_storage
from backend.api.core.video_merge import extract_last_frame, find_ffmpeg
from backend.api.db_helpers import (
    create_initial_item,
    get_asset_any_user,
    get_items_by_generation_id,
    locate_item,
    set_item_fields,
    update_item_failed,
    update_item_success,
)

logger = logging.getLogger("pltt_video_studio_2.generate_video")

def _unavailable(errors: list[str]) -> RuntimeError:
    """Provider and key details go to the server log; users get a plain message."""
    logger.warning("video generation failed: %s", "; ".join(errors) or "no provider configured")
    return RuntimeError("Video generation failed. Please try again." if errors else "Video generation isn't available right now.")

router = PluginRouter(tags=["generate-video"])

#: How long a platform clip is polled before it is collected from its handle.
PLATFORM_TIMEOUT = 600


#: "<video asset id>#last" → the stored URL of that clip's last frame (made once per clip).
_last_frames: dict[str, str] = {}


async def last_frame_url(ctx: PluginContext, video_asset_id: str) -> Optional[str]:
    """The stored last frame of a generated clip (extracted with ffmpeg on first use, then reused) — where the
    next clip of a continuous (Flow) film starts."""
    if video_asset_id in _last_frames:
        return _last_frames[video_asset_id]
    row = await get_asset_any_user(ctx.db, video_asset_id)
    if row is None or row.type != "video" or not row.url:
        return None
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        logger.warning("no ffmpeg: clip %s starts from its first frame instead of the last", video_asset_id)
        return None
    data = await fetch_media_bytes(row.url)
    with tempfile.TemporaryDirectory(prefix="vs2-last-") as tmp:
        video, still = str(Path(tmp) / "clip.mp4"), str(Path(tmp) / "last.jpg")
        Path(video).write_bytes(data)
        await anyio.to_thread.run_sync(functools.partial(extract_last_frame, ffmpeg, video, still))
        url = await upload_bytes_to_storage(ctx, Path(still).read_bytes(), "image/jpeg", folder="frames", ext=".jpg")
    _last_frames[video_asset_id] = url
    return url


async def resolve_frame(ctx: PluginContext, ref: Optional[str]) -> Optional[str]:
    """A start/end/reference frame given as an asset id or URL → a URL a
    provider can fetch. Video assets contribute their first frame (poster); "<video asset id>#last" is that clip's
    last frame (Flow: the next clip continues from it)."""
    if not ref:
        return None
    if ref.endswith("#last"):
        try:
            url = await last_frame_url(ctx, ref[: -len("#last")])
        except Exception as exc:  # noqa: BLE001 - the clip's poster then
            logger.warning("last frame of %s unavailable: %s", ref, exc)
            url = None
        if url:
            return provider_safe_media_url(url)
        ref = ref[: -len("#last")]
    url = ref
    if not ref.startswith(("http://", "https://", "data:", "file://", "/")):
        row = await get_asset_any_user(ctx.db, ref)
        if not row:
            return None
        url = row.poster_url if row.type == "video" else row.url
        if row.type == "audio" or not url:
            return None
    return provider_safe_media_url(url)


async def platform_clip(ctx: PluginContext, model: str, spec: Dict[str, Any], kwargs: Dict[str, Any]) -> Optional[str]:
    """The clip from the platform's media service (`ctx.media`, the org's budget) when its catalog lists this
    model's fal endpoint; None (without a call) when it doesn't. Raises when the platform tried and failed."""
    try:
        platform_id, fields = video_request(
            model, start_image=spec.get("start_image"), end_image=spec.get("end_image"),
            image_references=spec.get("image_references"), elements=spec.get("elements"), **kwargs,
        )
    except ValueError:
        return None
    if not await platform_models.media_available(ctx, platform_id):
        return None
    async with log_call("ctx.media", "video", model=platform_id) as _log:
        res = await platform_models.platform_run(
            ctx, platform_id, build_payload(model, spec["prompt"], **fields), timeout=PLATFORM_TIMEOUT,
        )
        url = platform_models.result_url(res, "video", "url")
        _log["videos"] = 1 if url else 0
    if not url:
        raise RuntimeError(f"no video url in {str(res)[:300]}")
    return url


def start_video_job(ctx: PluginContext, spec: Dict[str, Any], generation_id: Optional[str] = None) -> str:
    """Kick off a clip generation in the background; returns generation_id.

    `spec` keys: prompt, model, duration, aspect_ratio, resolution, audio,
    start_image, end_image, image_references, elements (all resolved URLs),
    project_id, scene_id, source, name, fallback_models."""
    org_id = ctx.organization_id
    generation_id = generation_id or str(uuid.uuid4())
    fal_key = read_secret(ctx, "FAL_KEY")
    runware_key = read_secret(ctx, "RUNWARE_KEY")
    models = [spec["model"], *[m for m in spec.get("fallback_models") or [] if m != spec["model"]]]

    async def _attempt(db, record_id: str, model: str, loop) -> str:
        duration = str(clamp_duration(model, spec.get("duration") or 5))
        kwargs: Dict[str, Any] = {"duration": duration, "aspect_ratio": fit_aspect_ratio(model, spec.get("aspect_ratio"))}
        # Only send a resolution the model's endpoint accepts ("auto" = none).
        resolution = str(spec.get("resolution") or "").lower()
        if resolution != "auto" and resolution in video_model(model)["resolutions"]:
            kwargs["resolution"] = resolution
        if spec.get("audio") is not None:
            kwargs["audio"] = bool(spec["audio"])

        def on_update(update):
            data = {"id": record_id, "generation_id": generation_id, "project_id": spec.get("project_id"),
                    "scene_id": spec.get("scene_id"), "status": "processing", "model_name": model}
            if isinstance(update, fal_client.InProgress):
                data["logs"] = [log["message"] for log in update.logs][-3:]
            elif isinstance(update, fal_client.Queued):
                data["position"] = update.position
            broadcast_event_threadsafe(loop, org_id, "processing", data, record_id, ctx.user_id)

        errors: List[str] = []
        try:
            url = await platform_clip(ctx, model, spec, kwargs)
            if url:
                return url
        except Exception as e:  # noqa: BLE001 — the app's own keys next
            errors.append(f"platform: {e}")
            logger.warning("platform video %s failed: %s", model, e)

        fal_err: Optional[Exception] = None
        if fal_key:
            try:
                res = await anyio.to_thread.run_sync(functools.partial(
                    generate_video, fal_key, model, spec["prompt"], on_progress=on_update,
                    start_image=spec.get("start_image"), end_image=spec.get("end_image"),
                    image_references=spec.get("image_references"), elements=spec.get("elements"),
                    **kwargs,
                ))
                url = (res.get("video") or {}).get("url") or res.get("url")
                if url:
                    return url
                fal_err = RuntimeError(f"Fal returned no video url: {str(res)[:300]}")
            except Exception as e:  # noqa: BLE001
                fal_err = e
                logger.warning("fal video %s failed: %s", model, e)
        if fal_err:
            errors.append(f"fal: {fal_err}")
        air = get_runware_video_model(model)
        if air and runware_key:
            try:
                return await generate_video_runware(
                    runware_key, air, spec["prompt"], kwargs["aspect_ratio"], duration,
                    resolution=kwargs.get("resolution"), start_image=spec.get("start_image"),
                    end_image=spec.get("end_image"), audio=kwargs.get("audio"),
                )
            except Exception as rw_err:  # noqa: BLE001
                errors.append(f"runware: {rw_err}")
        raise _unavailable(errors)

    async def _bg():
        async with standalone_background_session(ctx) as db:
            loop = asyncio.get_running_loop()
            doc = await create_initial_item(
                db, organization_id=org_id, user_id=ctx.user_id, type="video", model_name=spec["model"],
                prompt=spec["prompt"], params={**spec, "duration": spec.get("duration")},
                project_id=spec.get("project_id"), scene_id=spec.get("scene_id"), generation_id=generation_id,
            )
            record_id = doc["id"]
            await broadcast_event(org_id, "started", doc, record_id, ctx.user_id)
            errors: List[str] = []
            for model in models:
                try:
                    url = await _attempt(db, record_id, model, loop)
                    asset = await store_asset_from_url(
                        ctx, db, url, kind="video", name=spec.get("name") or "Generated clip",
                        source=spec.get("source") or "generated", category="Videos",
                        project_id=spec.get("project_id"), item_id=record_id,
                    )
                    await update_item_success(db, record_id, url=asset["src"])
                    final = await set_item_fields(db, record_id, {
                        "asset_id": asset["id"], "thumbnail_url": asset.get("poster"), "model_name": model,
                    })
                    await broadcast_event(org_id, "completed", {**(final or {}), "asset": asset}, record_id, ctx.user_id)
                    return
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{model}: {e}")
                    logger.warning("video attempt with %s failed: %s", model, e)
            mark_failed(" | ".join(errors)[:3000])
            failed = await update_item_failed(db, record_id, " | ".join(errors)[:3000])
            await broadcast_event(org_id, "failed", failed, record_id, ctx.user_id)

    bind_user_id(ctx.user_id)
    asyncio.create_task(run_task("video_generation", _bg(), project_id=spec.get("project_id") or generation_id))
    return generation_id


def _require_video_provider(ctx: PluginContext) -> None:
    if not (read_secret(ctx, "FAL_KEY") or read_secret(ctx, "RUNWARE_KEY") or platform_models.media_service(ctx)):
        logger.warning("video generation unavailable: no video provider key (FAL_KEY / RUNWARE_KEY) or media service")
        raise HTTPException(status_code=503, detail="Video generation isn't available right now.")


class VideoRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    models: Optional[List[str]] = None
    duration: Optional[float] = None
    aspect_ratio: Optional[str] = "16:9"
    resolution: Optional[str] = None
    audio: Optional[bool] = True
    # Asset ids or URLs.
    start_image: Optional[str] = None
    end_image: Optional[str] = None
    image_references: Optional[List[str]] = None
    elements: Optional[List[Dict[str, Any]]] = None
    project_id: Optional[str] = None
    scene_id: Optional[str] = None
    source: Optional[str] = None
    name: Optional[str] = None


@router.post("/generate/video", dependencies=[require_permission("resources:write")])
async def generate_video_endpoint(body: VideoRequest, ctx: PluginContext = Depends(get_ctx)):
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    _require_video_provider(ctx)
    model = video_model(body.model or (body.models or [DEFAULT_VIDEO_MODEL])[0])["id"]
    refs = [r for r in [await resolve_frame(ctx, x) for x in (body.image_references or [])] if r]
    spec = {
        "prompt": prompt, "model": model, "duration": body.duration or 5,
        "aspect_ratio": body.aspect_ratio or "16:9", "resolution": body.resolution, "audio": body.audio,
        "start_image": await resolve_frame(ctx, body.start_image),
        "end_image": await resolve_frame(ctx, body.end_image) if video_model(model)["end_frame"] else None,
        "image_references": refs or None, "elements": body.elements,
        "project_id": body.project_id, "scene_id": body.scene_id,
        "source": body.source or "generated", "name": body.name,
    }
    return {"status": "pending", "generation_id": start_video_job(ctx, spec)}


class RetryRequest(BaseModel):
    item_id: Optional[str] = None
    generation_id: Optional[str] = None
    model: Optional[str] = None


@router.post("/generate/video/retry", dependencies=[require_permission("resources:write")])
async def retry_video(body: RetryRequest, ctx: PluginContext = Depends(get_ctx)):
    """Re-run a clip from its saved inputs, optionally on another model."""
    item = None
    if body.item_id:
        item = await locate_item(ctx.db, ctx.user_id, body.item_id)
    elif body.generation_id:
        items = await get_items_by_generation_id(ctx.db, ctx.user_id, body.generation_id)
        item = items[-1] if items else None
    if not item or item.get("type") != "video":
        raise HTTPException(status_code=404, detail="Generation not found")
    _require_video_provider(ctx)
    spec = dict(item.get("gen_params") or {})
    if not spec.get("prompt"):
        spec["prompt"] = item.get("prompt") or ""
    spec["model"] = video_model(body.model or spec.get("model") or item["model_name"])["id"]
    return {"status": "pending", "generation_id": start_video_job(ctx, spec)}


class CameraRequest(BaseModel):
    project_id: Optional[str] = None
    scene_id: Optional[str] = None
    asset_id: str
    preset: str = "Custom path"
    keyframes: List[Dict[str, Any]] = []
    duration: float = 6
    resolution: Optional[str] = "480P"
    aspect_ratio: Optional[str] = "16:9"
    instructions: Optional[str] = ""
    quality_retry: bool = True
    model: Optional[str] = None


@router.post("/generate/camera", dependencies=[require_permission("resources:write")])
async def generate_camera_move(body: CameraRequest, ctx: PluginContext = Depends(get_ctx)):
    """Animate the first frame with only the camera moving along the path
    drawn in the camera dialog."""
    _require_video_provider(ctx)
    start = await resolve_frame(ctx, body.asset_id)
    if not start:
        raise HTTPException(status_code=400, detail="Choose an image or video for the first frame.")
    model = video_model(body.model or "kling_3_0_pro")["id"]
    res = (body.resolution or "480P").lower()
    spec = {
        "prompt": build_camera_prompt(body.preset, body.keyframes, body.duration, body.instructions or ""),
        "model": model, "duration": body.duration, "aspect_ratio": body.aspect_ratio or "16:9",
        "resolution": "720p" if res in ("768p", "1080p") else "480p", "audio": False,
        "start_image": start, "project_id": body.project_id, "scene_id": body.scene_id,
        "source": "camera", "name": f"Camera · {body.preset}",
        "fallback_models": ["seedance_2_0"] if body.quality_retry else [],
    }
    return {"status": "pending", "generation_id": start_video_job(ctx, spec)}
