"""Image generation: scene stills and Library images.

Provider order per model (from the storyboard app): the platform's media
service (`ctx.media`, only when its catalog lists the model's exact fal
endpoint) → pltt LLM Router (when enabled) → Fal → Runware; Midjourney goes
router → TTAPI (imagine + U1).
Each finished image is copied into app storage and registered as a Library
asset linked to the project (category "Scenes" for story stills, else
"Images"); the `completed` SSE event carries the asset.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from typing import Any, Dict, List, Optional

import anyio
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from palette_sdk import PluginContext, PluginRouter, require_permission

from backend.api.core.std_logs import call as log_call, mark_failed, run_task
from backend.api.core.assets_service import store_asset_from_url
from backend.api.core.bg_session import standalone_background_session
from backend.api.core.catalog import resolve_image_model
from backend.api.core import platform_models
from backend.api.core.fal_helper import build_payload, generate_image, image_model_id
from backend.api.core.midjourney_helper import (
    _build_prompt as build_midjourney_prompt,
    fetch_midjourney_job,
    perform_midjourney_action,
    submit_midjourney_job,
)
from backend.api.core.org_db import get_ctx
from backend.api.core.router_helper import (
    RouterUnavailable,
    apply_secrets as apply_router_secrets,
    edit_image_router,
    generate_image_router,
    midjourney_generate_router,
)
from backend.api.core.runware_helper import aspect_ratio_to_dimensions, generate_image_runware, get_runware_model
from backend.api.core.secrets import read_secret
from backend.api.core.sse_manager import bind_user_id, broadcast_event
from backend.api.routes.generate_video import resolve_frame
from backend.api.db_helpers import create_initial_item, set_item_fields, update_item_failed, update_item_success

logger = logging.getLogger("pltt_video_studio_2.generate_image")

def _unavailable(errors: list[str]) -> RuntimeError:
    """Provider and key details go to the server log; users get a plain message."""
    logger.warning("image generation failed: %s", "; ".join(errors) or "no provider configured")
    return RuntimeError("Image generation failed. Please try again." if errors else "Image generation isn't available right now.")

router = PluginRouter(tags=["generate-image"])

PROVIDER_TIMEOUT = 300
_QUALITY = {"auto": "medium", "low": "low", "medium": "medium", "high": "high",
            "낮음": "low", "중간": "medium", "높음": "high"}


def _payload(model: str, params: dict, refs: list[str], count: int) -> dict:
    """Provider payload per Fal model (ported from storyboard)."""
    ar = params.get("aspect_ratio") or "16:9"
    res = str(params.get("resolution") or "1K").upper()
    out: dict = {"num_images": count}
    if "nano_banana" in model:
        ratios = {"auto", "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16"}
        out["resolution"] = res if res in {"1K", "2K", "4K"} else "1K"
        if ar in ratios:
            out["aspect_ratio"] = ar
        if refs:
            out["image_urls"] = refs
    elif "flux" in model:
        sizes = {"1:1": "square_hd", "16:9": "landscape_16_9", "9:16": "portrait_16_9", "4:3": "landscape_4_3", "3:4": "portrait_4_3"}
        out["image_size"] = sizes.get(ar, "landscape_16_9")
    elif "openai" in model:
        out["quality"] = _QUALITY.get(str(params.get("quality") or "auto").lower(), "medium")
        if refs:
            out["image_urls"] = refs
        base = {"1K": 1024, "2K": 2048, "4K": 4096}.get(res, 1024)
        try:
            wr, hr = (float(x) for x in ar.split(":"))
        except Exception:
            wr, hr = 1.0, 1.0
        w, h = (base, int(base * hr / wr)) if wr >= hr else (int(base * wr / hr), base)
        out["image_size"] = {"width": w // 16 * 16, "height": h // 16 * 16}
    return out


async def _generate_urls(ctx: PluginContext, model: str, prompt: str, params: dict, refs: list[str], count: int) -> list[str]:
    """Image URLs from the first provider that succeeds."""
    errors: list[str] = []
    if model == "midjourney":
        try:
            r = await midjourney_generate_router(build_midjourney_prompt(prompt, params.get("aspect_ratio"), refs))
            if r.get("image_url"):
                return [r["image_url"]]
        except RouterUnavailable:
            pass
        except Exception as e:  # noqa: BLE001
            errors.append(f"router: {e}")
        key, fallback = read_secret(ctx, "MIDJOURNEY_KEY"), read_secret(ctx, "MIDJOURNEY_TT_API_KEY_FALLBACK")
        if not (key or fallback):
            raise _unavailable(errors)
        job = await submit_midjourney_job(key, prompt, aspect_ratio=params.get("aspect_ratio"),
                                          image_references=refs, fallback_api_key=fallback)
        await fetch_midjourney_job(key, job, fallback_api_key=fallback)
        u1 = await perform_midjourney_action(key, job, "U1", fallback_api_key=fallback)
        return [u1["image_url"]]

    # The platform first (ctx.media, the org's budget) — only when its catalog lists this exact fal endpoint.
    fields = _payload(model, params, refs, count)
    try:
        platform_id = image_model_id(model, **fields)
    except ValueError:
        platform_id = None
    if platform_id and await platform_models.media_available(ctx, platform_id):
        try:
            async with log_call("ctx.media", "image", model=platform_id) as _log:
                res = await platform_models.platform_run(
                    ctx, platform_id, build_payload(model, prompt, **fields), timeout=PROVIDER_TIMEOUT,
                )
                urls = [i.get("url") for i in res.get("images") or [] if isinstance(i, dict) and i.get("url")]
                if not urls and platform_models.result_url(res, "image"):
                    urls = [platform_models.result_url(res, "image")]
                _log["images"] = len(urls)
            if urls:
                return urls
            errors.append("platform: no images returned")
        except Exception as e:  # noqa: BLE001
            errors.append(f"platform: {e}")
            logger.warning("platform image %s failed: %s", platform_id, e)

    try:
        if refs:
            urls = await asyncio.wait_for(edit_image_router(model, prompt, refs[0], refs[1:]), PROVIDER_TIMEOUT)
        else:
            urls = await asyncio.wait_for(generate_image_router(
                model, prompt, aspect_ratio=params.get("aspect_ratio"),
                resolution=params.get("resolution"), num_images=count,
            ), PROVIDER_TIMEOUT)
        if urls:
            return urls
    except RouterUnavailable:
        pass
    except Exception as e:  # noqa: BLE001
        errors.append(f"router: {e}")

    fal_key = read_secret(ctx, "FAL_KEY")
    if fal_key:
        try:
            res = await asyncio.wait_for(anyio.to_thread.run_sync(functools.partial(
                generate_image, fal_key, model, prompt, **_payload(model, params, refs, count),
            )), PROVIDER_TIMEOUT)
            urls = [i.get("url") for i in res.get("images", []) if i.get("url")]
            if urls:
                return urls
            errors.append("fal: no images returned")
        except Exception as e:  # noqa: BLE001
            errors.append(f"fal: {e}")

    runware_key = read_secret(ctx, "RUNWARE_KEY")
    air = get_runware_model(model)
    if air and runware_key:
        try:
            w, h = aspect_ratio_to_dimensions(params.get("aspect_ratio") or "16:9", params.get("resolution") or "1K", air)
            urls = await generate_image_runware(runware_key, air, prompt, w, h, count)
            if urls:
                return urls
        except Exception as e:  # noqa: BLE001
            errors.append(f"runware: {e}")
    raise _unavailable(errors)


def start_image_job(ctx: PluginContext, spec: Dict[str, Any]) -> str:
    """Background image generation; returns generation_id. `spec`: prompt,
    model, params{aspect_ratio,resolution,quality}, count, refs (URLs),
    project_id, scene_id, source, name, category."""
    org_id = ctx.organization_id
    generation_id = spec.get("generation_id") or str(uuid.uuid4())
    count = max(1, min(4, int(spec.get("count") or 1)))
    if spec["model"] == "midjourney":
        count = 1

    async def _bg():
        async with standalone_background_session(ctx) as db:
            docs = []
            for _ in range(count):
                doc = await create_initial_item(
                    db, organization_id=org_id, user_id=ctx.user_id, type="image", model_name=spec["model"],
                    prompt=spec["prompt"], params={**spec.get("params", {}), **spec},
                    project_id=spec.get("project_id"), scene_id=spec.get("scene_id"), generation_id=generation_id,
                )
                docs.append(doc)
                await broadcast_event(org_id, "started", doc, doc["id"], ctx.user_id)
            try:
                urls = await _generate_urls(ctx, spec["model"], spec["prompt"], spec.get("params") or {}, spec.get("refs") or [], count)
            except Exception as e:  # noqa: BLE001
                logger.warning("image generation failed: %s", e)
                for d in docs:
                    mark_failed(str(e))
                    failed = await update_item_failed(db, d["id"], str(e))
                    await broadcast_event(org_id, "failed", failed, d["id"], ctx.user_id)
                return
            for i, d in enumerate(docs):
                if i >= len(urls):
                    mark_failed("The provider returned fewer images than requested")
                    failed = await update_item_failed(db, d["id"], "The provider returned fewer images than requested")
                    await broadcast_event(org_id, "failed", failed, d["id"], ctx.user_id)
                    continue
                try:
                    asset = await store_asset_from_url(
                        ctx, db, urls[i], kind="image", name=spec.get("name") or "Generated image",
                        source="generated", category=spec.get("category") or "Images",
                        project_id=spec.get("project_id"), item_id=d["id"],
                    )
                    await update_item_success(db, d["id"], url=asset["src"])
                    final = await set_item_fields(db, d["id"], {"asset_id": asset["id"]})
                    await broadcast_event(org_id, "completed", {**(final or {}), "asset": asset}, d["id"], ctx.user_id)
                except Exception as e:  # noqa: BLE001
                    mark_failed(f"storage: {e}")
                    failed = await update_item_failed(db, d["id"], f"storage: {e}")
                    await broadcast_event(org_id, "failed", failed, d["id"], ctx.user_id)

    bind_user_id(ctx.user_id)
    apply_router_secrets(ctx)
    asyncio.create_task(run_task("image_generation", _bg(), project_id=spec.get("project_id") or generation_id))
    return generation_id


class ImageRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    models: Optional[List[str]] = None
    aspect_ratio: Optional[str] = "16:9"
    resolution: Optional[str] = "1K"
    quality: Optional[str] = "auto"
    count: int = 1
    # Asset ids or URLs used as visual references (characters, locations…).
    references: Optional[List[str]] = None
    project_id: Optional[str] = None
    scene_id: Optional[str] = None
    source: Optional[str] = None
    name: Optional[str] = None
    generation_id: Optional[str] = None


@router.post("/generate/image", dependencies=[require_permission("resources:write")])
async def generate_image_endpoint(body: ImageRequest, ctx: PluginContext = Depends(get_ctx)):
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    model = resolve_image_model(body.model or (body.models or [None])[0])
    apply_router_secrets(ctx)
    if model == "midjourney":
        if not (read_secret(ctx, "MIDJOURNEY_KEY") or read_secret(ctx, "MIDJOURNEY_TT_API_KEY_FALLBACK")
                or read_secret(ctx, "LLM_ROUTER_API_KEY")):
            logger.warning("image generation unavailable: MIDJOURNEY_KEY is not configured")
            raise HTTPException(status_code=503, detail="This image model isn't available right now.")
    elif not (read_secret(ctx, "FAL_KEY") or read_secret(ctx, "RUNWARE_KEY") or read_secret(ctx, "LLM_ROUTER_API_KEY")
              or platform_models.media_service(ctx)):
        logger.warning("image generation unavailable: no image provider key (FAL_KEY / RUNWARE_KEY) or media service")
        raise HTTPException(status_code=503, detail="Image generation isn't available right now.")
    refs = [r for r in [await resolve_frame(ctx, x) for x in (body.references or [])[:7]] if r]
    res = (body.resolution or "1K")
    spec = {
        "prompt": prompt, "model": model, "count": body.count, "refs": refs,
        "params": {"aspect_ratio": None if body.aspect_ratio == "Auto" else body.aspect_ratio,
                   "resolution": "1K" if res.lower() == "auto" else res, "quality": body.quality},
        "project_id": body.project_id, "scene_id": body.scene_id, "source": body.source or "generated",
        "name": body.name, "category": "Scenes" if body.source == "story_scene" else "Images",
        "generation_id": body.generation_id,
    }
    return {"status": "pending", "generation_id": start_image_job(ctx, spec)}
