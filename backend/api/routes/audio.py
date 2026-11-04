"""Voice-over (the platform's ElevenLabs voice first, then ElevenLabs TTS on
the app's key) and music (Eleven Music, the app's key) for the project's
voice / music tracks. Both results become Library audio assets."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from palette_sdk import PluginContext, PluginRouter, require_permission

from backend.api.core import elevenlabs_helper, platform_models
from backend.api.core.std_logs import mark_failed, run_task
from backend.api.core.assets_service import store_asset_bytes
from backend.api.core.bg_session import standalone_background_session
from backend.api.core.org_db import get_ctx
from backend.api.core.secrets import read_secret
from backend.api.core.sse_manager import bind_user_id, broadcast_event
from backend.api.db_helpers import create_initial_item, set_item_fields, update_item_failed, update_item_success

logger = logging.getLogger("pltt_video_studio_2.audio")
router = PluginRouter(tags=["audio"])


def _eleven_key(ctx: PluginContext) -> str:
    key = read_secret(ctx, "ELEVEN_API_KEY")
    if not key:
        logger.warning("audio unavailable: ELEVEN_API_KEY is not configured")
        raise HTTPException(status_code=503, detail="Voice and music generation aren't available right now.")
    return key


@router.get("/audio/voices", dependencies=[require_permission("resources:read")])
async def voices(ctx: PluginContext = Depends(get_ctx)):
    key = read_secret(ctx, "ELEVEN_API_KEY")
    if not key:
        # No ElevenLabs account of the app's own: the platform's premade ElevenLabs voices, by name.
        if await platform_models.media_available(ctx, platform_models.PLATFORM_TTS):
            return {"available": True, "provider": "platform",
                    "voices": [{"id": n, "name": n} for n in elevenlabs_helper.PLATFORM_VOICES]}
        return {"available": False, "voices": []}
    try:
        return {"available": True, "voices": await elevenlabs_helper.list_voices(key)}
    except Exception as e:  # noqa: BLE001
        logger.warning("could not list voices: %s", e)
        return {"available": True, "voices": [], "error": str(e)}


class TtsRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None
    language: Optional[str] = None
    project_id: Optional[str] = None
    scene_id: Optional[str] = None
    name: Optional[str] = None


@router.post("/audio/tts", dependencies=[require_permission("resources:write")])
async def text_to_speech(body: TtsRequest, ctx: PluginContext = Depends(get_ctx)):
    """Narrate `text`; returns the voice asset and caption lines timed from
    the word alignment (relative to the start of the clip)."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Enter a script first.")
    key = read_secret(ctx, "ELEVEN_API_KEY")
    if not key and not await platform_models.media_available(ctx, platform_models.PLATFORM_TTS):
        logger.warning("audio unavailable: ELEVEN_API_KEY is not configured")
        raise HTTPException(status_code=503, detail="Voice and music generation aren't available right now.")
    try:
        # The platform's ElevenLabs voice first (the chosen voice), then the app's own ElevenLabs key.
        audio, words, _provider = await elevenlabs_helper.narrate(
            ctx, key, text[:5000], body.voice_id or None,
            language_code=body.language if body.language in ("en", "ko", "ja") else None,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Voice generation failed: {e}")
    asset = await store_asset_bytes(
        ctx, ctx.db, audio, mime="audio/mpeg", name=(body.name or f"Voice · {text[:28]}") + ".mp3",
        folder="outputs/audio", source="tts", category="Voices & audio", project_id=body.project_id,
    )
    return {"asset": asset, "captions": elevenlabs_helper.group_captions(words)}


class MusicRequest(BaseModel):
    prompt: str
    duration_seconds: float = 30
    project_id: Optional[str] = None


@router.post("/audio/music", dependencies=[require_permission("resources:write")])
async def generate_music(body: MusicRequest, ctx: PluginContext = Depends(get_ctx)):
    """Compose a music bed in the background (it can take a minute)."""
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Describe the music first.")
    key = _eleven_key(ctx)
    seconds = max(5.0, min(120.0, float(body.duration_seconds or 30)))
    org_id = ctx.organization_id
    generation_id = str(uuid.uuid4())
    bind_user_id(ctx.user_id)

    async def _bg():
        async with standalone_background_session(ctx) as db:
            doc = await create_initial_item(
                db, organization_id=org_id, user_id=ctx.user_id, type="audio", model_name="eleven_music",
                prompt=prompt, params={"duration": seconds}, project_id=body.project_id,
                generation_id=generation_id,
            )
            await broadcast_event(org_id, "started", doc, doc["id"], ctx.user_id)
            try:
                data = await elevenlabs_helper.generate_music(key, prompt, int(seconds * 1000))
                asset = await store_asset_bytes(
                    ctx, db, data, mime="audio/mpeg", name=f"Music · {prompt[:28]}.mp3",
                    folder="outputs/audio", source="music", category="Voices & audio",
                    project_id=body.project_id, item_id=doc["id"],
                )
                await update_item_success(db, doc["id"], url=asset["src"])
                final = await set_item_fields(db, doc["id"], {"asset_id": asset["id"]})
                await broadcast_event(org_id, "completed", {**(final or {}), "asset": asset}, doc["id"], ctx.user_id)
            except Exception as e:  # noqa: BLE001
                mark_failed(str(e))
                failed = await update_item_failed(db, doc["id"], str(e))
                await broadcast_event(org_id, "failed", failed, doc["id"], ctx.user_id)

    asyncio.create_task(run_task("music_generation", _bg(), project_id=body.project_id or generation_id))
    return {"status": "pending", "generation_id": generation_id}
