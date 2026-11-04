"""Library assets: upload, list, classify, bin, archive, analyze, dev media."""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
from typing import Any, Dict, List, Optional

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from palette_sdk import PluginContext, PluginRouter, require_permission

from backend.api.core.assets_service import kind_for_mime, store_asset_bytes, suffix_for
from backend.api.core.llm import llm_json
from backend.api.core.org_db import get_ctx
from backend.api.core.storage_helper import fetch_media_bytes, input_key, local_path_for
from backend.api.db_helpers import (
    ASSET_CATEGORIES,
    delete_asset,
    find_asset_by_hash,
    get_asset,
    get_asset_any_user,
    list_assets,
    patch_asset,
)

logger = logging.getLogger("pltt_video_studio_2.assets")
router = PluginRouter(tags=["assets"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # matches the browser-side limit


@router.get("/assets", dependencies=[require_permission("resources:read")])
async def get_assets(include_trashed: bool = True, ctx: PluginContext = Depends(get_ctx)):
    return {"assets": await list_assets(ctx.db, ctx.user_id, include_trashed=include_trashed)}


@router.post("/assets/upload", dependencies=[require_permission("resources:write")])
async def upload_asset(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    source: str = Form("upload"),
    ctx: PluginContext = Depends(get_ctx),
):
    """Upload an image, video or audio file (also used for screen/voice
    recordings). Identical bytes are deduplicated per user."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Files can be up to 200 MB each.")
    mime = (file.content_type or mimetypes.guess_type(file.filename or "")[0] or "").split(";")[0]
    kind = kind_for_mime(mime)
    if not kind:
        raise HTTPException(status_code=415, detail="Only images, video and audio are supported.")

    content_hash = hashlib.sha256(data).hexdigest()
    existing = await find_asset_by_hash(ctx.db, ctx.user_id, content_hash)
    if existing:
        if project_id and not existing.get("projectId"):
            existing = await patch_asset(ctx.db, ctx.user_id, existing["id"], {"project_id": project_id}) or existing
        return {"status": "exists", "asset": existing}

    display = (name or file.filename or f"{kind}{suffix_for('', mime)}").strip()
    cat = category if category in ASSET_CATEGORIES else None
    try:
        asset = await store_asset_bytes(
            ctx, ctx.db, data, mime=mime, name=display, folder="inputs",
            source="recording" if source == "recording" else "upload",
            category=cat, project_id=project_id, content_hash=content_hash,
            storage_key=input_key(ctx.user_id, content_hash, suffix_for(display, mime)),
            uploaded=True,
        )
    except Exception as exc:
        logger.exception("upload failed")
        raise HTTPException(status_code=500, detail=f"Could not save the file: {exc}")
    return {"status": "created", "asset": asset}


class AssetPatch(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    trashed: Optional[bool] = None
    archived: Optional[bool] = None
    project_id: Optional[str] = None


@router.patch("/assets/{asset_id}", dependencies=[require_permission("resources:write")])
async def update_asset(asset_id: str, body: AssetPatch, ctx: PluginContext = Depends(get_ctx)):
    asset = await patch_asset(ctx.db, ctx.user_id, asset_id, body.model_dump(exclude_unset=True))
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return {"asset": asset}


class BulkPatch(BaseModel):
    ids: List[str]
    patch: AssetPatch


@router.post("/assets/bulk", dependencies=[require_permission("resources:write")])
async def bulk_update_assets(body: BulkPatch, ctx: PluginContext = Depends(get_ctx)):
    patch = body.patch.model_dump(exclude_unset=True)
    out = []
    for asset_id in body.ids[:200]:
        a = await patch_asset(ctx.db, ctx.user_id, asset_id, patch)
        if a:
            out.append(a)
    return {"assets": out}


@router.delete("/assets/{asset_id}", dependencies=[require_permission("resources:write")])
async def remove_asset(asset_id: str, ctx: PluginContext = Depends(get_ctx)):
    if not await delete_asset(ctx.db, ctx.user_id, asset_id):
        raise HTTPException(status_code=404, detail="Asset not found")
    return {"status": "deleted"}


_ANALYZE_SYSTEM = """You analyse ONE reference image that will guide AI image and video generation for a multi-scene film.

Return ONLY a JSON object of this exact shape:
{"kind": "character" | "object" | "background" | "style" | "other", "name": "...", "description": "..."}

Rules:
- "kind": what the image primarily provides — a character (person/creature), an object/prop, a background/location, an overall art style, or other.
- "name": a short 1–3 word handle (e.g. "red-haired girl", "vintage taxi", "neon alley").
- "description": everything an image model needs to reproduce the subject IDENTICALLY in new scenes. Characters: apparent age, gender, face, hair, skin, build, wardrobe, accessories, distinguishing marks. Objects: shape, colors, materials, wear, markings. Backgrounds: setting, architecture, palette, lighting, era, weather. Styles: medium, technique, palette, mood.
- Describe only what is visible; be concrete and specific."""

_SUGGESTED = {"character": "Characters", "background": "Locations"}


def _clean_analysis(data: dict) -> dict:
    kind = str(data.get("kind") or "other").strip().lower()
    if kind not in {"character", "object", "background", "style", "other"}:
        kind = "other"
    return {
        "kind": kind,
        "name": str(data.get("name") or "reference").strip()[:80],
        "description": str(data.get("description") or "").strip()[:2000],
    }


async def analyze_asset_row(ctx: Any, asset_id: str, llm: Optional[str] = None, language: Optional[str] = None) -> dict:
    """Vision analysis of an image (or a video's poster), cached on the row."""
    asset = await get_asset(ctx.db, ctx.user_id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    cached = asset.get("analysis")
    if isinstance(cached, dict) and cached.get("description"):
        return cached
    row = await get_asset_any_user(ctx.db, asset_id)
    source_url = row.poster_url if asset["type"] == "video" else row.url
    if asset["type"] == "audio" or not source_url:
        raise HTTPException(status_code=400, detail="Only images and videos can be analysed.")
    data = await fetch_media_bytes(source_url)
    mime = "image/jpeg" if asset["type"] == "video" else (row.mime or "image/png")
    result = _clean_analysis(await llm_json(
        ctx, _ANALYZE_SYSTEM, "Analyse this reference image for the film.",
        llm=llm, image_b64=base64.b64encode(data).decode("ascii"), mime=mime, language=language,
    ))
    await patch_asset(ctx.db, ctx.user_id, asset_id, {"analysis": result})
    return result


class AnalyzeBody(BaseModel):
    llm: Optional[str] = None
    language: Optional[str] = None
    apply_category: bool = False


@router.post("/assets/{asset_id}/analyze", dependencies=[require_permission("resources:write")])
async def analyze_asset(asset_id: str, body: AnalyzeBody, ctx: PluginContext = Depends(get_ctx)):
    """Classify an image as character / background / object with a vision
    model; optionally file it under Characters or Locations."""
    analysis = await analyze_asset_row(ctx, asset_id, body.llm, body.language)
    suggested = _SUGGESTED.get(analysis["kind"])
    asset = None
    if body.apply_category and suggested:
        asset = await patch_asset(ctx.db, ctx.user_id, asset_id, {"category": suggested})
    return {"analysis": analysis, "suggested_category": suggested, "asset": asset}


@router.get("/media/{asset_id}", dependencies=[require_permission("resources:read")])
async def serve_media(asset_id: str, poster: int = 0, ctx: PluginContext = Depends(get_ctx)):
    """Serve an asset stored in local dev storage (file:// URLs can't be
    loaded by the page). Hosted storage URLs are redirected to."""
    row = await get_asset_any_user(ctx.db, asset_id)
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    url = row.poster_url if poster else row.url
    if not url:
        raise HTTPException(status_code=404, detail="No media")
    path = local_path_for(url)
    if path is None:
        return RedirectResponse(url)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Media file is missing")
    media_type = "image/jpeg" if poster else (row.mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    return FileResponse(str(path), media_type=media_type)
