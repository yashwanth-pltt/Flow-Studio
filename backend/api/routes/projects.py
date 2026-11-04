"""Projects, archive versions, project chat and MP4 export."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import anyio
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from palette_sdk import PluginContext, PluginRouter, require_permission

from backend.api.core import timeline
from backend.api.core.std_logs import mark_failed, run_task
from backend.api.core.assets_service import store_asset_bytes
from backend.api.core.bg_session import standalone_background_session
from backend.api.core.llm import llm_json
from backend.api.core.org_db import get_ctx
from backend.api.core.sse_manager import bind_user_id, broadcast_event
from backend.api.core.storage_helper import fetch_media_bytes
from backend.api.core.video_merge import ffmpeg_unavailable_reason, find_ffmpeg
from backend.api.db_helpers import (
    add_message,
    create_initial_item,
    create_version,
    delete_version,
    get_asset_any_user,
    get_project,
    list_assets,
    list_messages,
    list_projects,
    list_versions,
    rename_project,
    set_item_fields,
    trash_project,
    update_item_failed,
    update_item_success,
    upsert_project,
)

logger = logging.getLogger("pltt_video_studio_2.projects")
router = PluginRouter(tags=["projects"])


# --- Projects -------------------------------------------------------------

@router.get("/projects", dependencies=[require_permission("resources:read")])
async def get_projects(ctx: PluginContext = Depends(get_ctx)):
    return {"projects": await list_projects(ctx.db, ctx.user_id)}


@router.get("/projects/{project_id}", dependencies=[require_permission("resources:read")])
async def get_one_project(project_id: str, ctx: PluginContext = Depends(get_ctx)):
    p = await get_project(ctx.db, ctx.user_id, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": p}


class ProjectSave(BaseModel):
    name: str
    doc: Dict[str, Any]
    base_rev: Optional[int] = None
    thumbnail_url: Optional[str] = None


@router.put("/projects/{project_id}", dependencies=[require_permission("resources:write")])
async def save_project(project_id: str, body: ProjectSave, ctx: PluginContext = Depends(get_ctx)):
    """Upsert the project document (the browser is its only writer)."""
    if len(project_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid project id")
    doc = dict(body.doc or {})
    for transient in ("id", "name", "updated", "rev"):
        doc.pop(transient, None)
    if len(json.dumps(doc)) > 5_000_000:
        raise HTTPException(status_code=413, detail="Project is too large to save")
    out = await upsert_project(
        ctx.db, organization_id=ctx.organization_id, user_id=ctx.user_id, project_id=project_id,
        name=(body.name or "Untitled project").strip() or "Untitled project", doc=doc,
        thumbnail_url=body.thumbnail_url, base_rev=body.base_rev,
    )
    return {"id": out["id"], "rev": out["rev"], "updated": out["updated"]}


class ProjectRename(BaseModel):
    name: str


@router.patch("/projects/{project_id}", dependencies=[require_permission("resources:write")])
async def patch_project(project_id: str, body: ProjectRename, ctx: PluginContext = Depends(get_ctx)):
    p = await rename_project(ctx.db, ctx.user_id, project_id, body.name.strip() or "Untitled project")
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": p}


@router.delete("/projects/{project_id}", dependencies=[require_permission("resources:write")])
async def remove_project(project_id: str, ctx: PluginContext = Depends(get_ctx)):
    if not await trash_project(ctx.db, ctx.user_id, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "deleted"}


# --- Versions (Archive) -----------------------------------------------------

@router.get("/versions", dependencies=[require_permission("resources:read")])
async def get_versions(ctx: PluginContext = Depends(get_ctx)):
    return {"versions": await list_versions(ctx.db, ctx.user_id)}


class VersionCreate(BaseModel):
    id: Optional[str] = None
    project_id: str
    name: str
    snapshot: Dict[str, Any]


@router.post("/versions", dependencies=[require_permission("resources:write")])
async def add_version(body: VersionCreate, ctx: PluginContext = Depends(get_ctx)):
    v = await create_version(
        ctx.db, organization_id=ctx.organization_id, user_id=ctx.user_id, version_id=body.id,
        project_id=body.project_id, name=body.name, snapshot=body.snapshot,
    )
    return {"version": v}


@router.delete("/versions/{version_id}", dependencies=[require_permission("resources:write")])
async def remove_version(version_id: str, ctx: PluginContext = Depends(get_ctx)):
    if not await delete_version(ctx.db, ctx.user_id, version_id):
        raise HTTPException(status_code=404, detail="Version not found")
    return {"status": "deleted"}


# --- Chat --------------------------------------------------------------------

_CHAT_SYSTEM = """You are Palette, the AI director inside a video editing studio. The user talks to you about ONE video project.

You get: the project's scenes (id, index, name, duration, script, caption, motion, whether it already has media), the project's reference media (with categories such as Characters and Locations), the selected scene, the generation settings, the run mode, and the user's message.

Return ONLY a JSON object:
{"reply": "...", "actions": [ ... ]}

"reply": 1–3 short friendly sentences shown in the chat, in the output language.
"actions": zero or more of:
- {"type": "plan", "brief": "<the full creative brief>"} — build (or rebuild) the storyboard from a brief. The video length is the user's total-duration setting and the planner fits the number of scenes to it; never promise a scene count or length in the reply. Use this when the user describes a video to make and the project has no scenes, or explicitly asks for a new storyboard.
- {"type": "update_scene", "scene_id": "<id>", "patch": {"name"?: "...", "script"?: "...", "caption"?: "...", "prompt"?: "<English image prompt>", "motion"?: "Still"|"Push in"|"Pan left", "transition"?: "Cut"|"Fade", "duration"?: <seconds>}} — edit one scene. Resolve scenes the user mentions by number, name or description; default to the selected scene.
- {"type": "generate", "scene_ids": ["<id>", ...] or [] for every scene without media, "stage": "image"|"video"|"both"} — create stills and/or animate clips.
- {"type": "voiceover", "script": "<narration for the whole video>"} — narrate the video.
- {"type": "music", "prompt": "<music description>"} — compose background music.
- {"type": "answer"} — just answer a question; no changes.

Rules:
- Prefer the smallest set of actions that does what the user asked.
- Image prompts are English and never contain on-screen text.
- Never invent scene ids — use only the ids given."""


class ChatRequest(BaseModel):
    text: str
    model: Optional[str] = None
    mode: str = "manual"
    language: Optional[str] = None
    selected_scene_id: Optional[str] = None
    generation: Optional[Dict[str, Any]] = None
    message_id: Optional[str] = None


def _scene_lines(doc: dict) -> str:
    rows = []
    for i, s in enumerate(doc.get("scenes") or []):
        rows.append(
            f'- id={s.get("id")} #{i + 1} "{s.get("name", "")}" {s.get("duration", 5)}s '
            f'media={"yes" if s.get("assetId") else "no"} motion={s.get("motion", "Still")} '
            f'script="{(s.get("script") or "")[:160]}" caption="{(s.get("caption") or "")[:60]}" '
            f'prompt="{(s.get("prompt") or "")[:200]}"'
        )
    return "\n".join(rows) or "(no scenes yet)"


@router.get("/projects/{project_id}/messages", dependencies=[require_permission("resources:read")])
async def get_messages(project_id: str, ctx: PluginContext = Depends(get_ctx)):
    return {"messages": await list_messages(ctx.db, ctx.user_id, project_id)}


@router.post("/projects/{project_id}/chat", dependencies=[require_permission("resources:write")])
async def chat(project_id: str, body: ChatRequest, ctx: PluginContext = Depends(get_ctx)):
    """One chat turn: stores the instruction, asks the selected text model
    what to do, stores its reply and returns the actions for the browser to
    apply (AUTO mode also runs the generation actions right away)."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message is empty")
    project = await get_project(ctx.db, ctx.user_id, project_id)
    doc = (project or {}).get("doc") or {}
    user_msg = await add_message(
        ctx.db, organization_id=ctx.organization_id, user_id=ctx.user_id, project_id=project_id,
        role="user", text=text, generation=body.generation, message_id=body.message_id,
    )
    media = [
        a for a in await list_assets(ctx.db, ctx.user_id, include_trashed=False)
        if a.get("projectId") == project_id or a["id"] in (doc.get("assetIds") or [])
    ]
    media_lines = "\n".join(
        f'- {a["id"]} [{a["category"]}] {a["type"]} "{a["name"]}"'
        + (f' — {a["analysis"]["description"][:160]}' if isinstance(a.get("analysis"), dict) and a["analysis"].get("description") else "")
        for a in media[:40]
    ) or "(none)"
    history = await list_messages(ctx.db, ctx.user_id, project_id, limit=12)
    history_lines = "\n".join(f'{m["role"]}: {m["text"][:300]}' for m in history[:-1])
    gen = body.generation or {}
    user = (
        f"Project: {(project or {}).get('name', 'Untitled project')}\n"
        f"Story: {doc.get('story') or '(none)'}\n\n"
        f"Scenes:\n{_scene_lines(doc)}\n\nReference media:\n{media_lines}\n\n"
        f"Selected scene: {body.selected_scene_id or '(none)'}\n"
        f"Generation settings: video engine {gen.get('engine')}, total video length {gen.get('duration')}s (fixed by the user), ratio {gen.get('ratio')}\n"
        f"Run mode: {body.mode}\n\n"
        + (f"Recent conversation:\n{history_lines}\n\n" if history_lines else "")
        + f"User message: {text}"
    )
    try:
        data = await llm_json(ctx, _CHAT_SYSTEM, user, body.model, language=body.language)
    except HTTPException as e:
        agent = await add_message(
            ctx.db, organization_id=ctx.organization_id, user_id=ctx.user_id, project_id=project_id,
            role="agent", text=f"⚠️ {e.detail}",
        )
        return {"user_message": user_msg, "agent_message": agent, "actions": [], "error": e.detail}

    valid_ids = {s.get("id") for s in doc.get("scenes") or []}
    actions: List[dict] = []
    for a in data.get("actions") or []:
        if not isinstance(a, dict):
            continue
        kind = a.get("type")
        if kind == "update_scene" and a.get("scene_id") in valid_ids and isinstance(a.get("patch"), dict):
            actions.append({"type": kind, "scene_id": a["scene_id"], "patch": a["patch"]})
        elif kind == "generate":
            ids = [i for i in (a.get("scene_ids") or []) if i in valid_ids]
            stage = a.get("stage") if a.get("stage") in ("image", "video", "both") else "both"
            actions.append({"type": kind, "scene_ids": ids, "stage": stage})
        elif kind == "plan" and str(a.get("brief") or "").strip():
            # Length and scene count come from the user's duration setting, not the chat model.
            actions.append({"type": kind, "brief": str(a["brief"]).strip(), "total_duration": None, "scene_count": None})
        elif kind == "voiceover" and str(a.get("script") or "").strip():
            actions.append({"type": kind, "script": str(a["script"]).strip()})
        elif kind == "music" and str(a.get("prompt") or "").strip():
            actions.append({"type": kind, "prompt": str(a["prompt"]).strip()})
    if not doc.get("scenes") and not any(a["type"] == "plan" for a in actions) and len(text) > 12:
        # A brief on an empty project always means "build it".
        actions.insert(0, {"type": "plan", "brief": text, "total_duration": None, "scene_count": None})
    reply = str(data.get("reply") or "").strip() or "Done."
    agent = await add_message(
        ctx.db, organization_id=ctx.organization_id, user_id=ctx.user_id, project_id=project_id,
        role="agent", text=reply, actions=actions,
    )
    return {"user_message": user_msg, "agent_message": agent, "actions": actions}


# --- Export -----------------------------------------------------------------

class ExportRequest(BaseModel):
    ratio: str = "16:9"
    resolution: str = "720p"
    bitrate: str = "high"
    burn_captions: bool = True
    name: Optional[str] = None


@router.post("/projects/{project_id}/export", dependencies=[require_permission("resources:write")])
async def export_project(project_id: str, body: ExportRequest, ctx: PluginContext = Depends(get_ctx)):
    """Render the saved timeline to MP4 in the background. Progress arrives
    over SSE; the finished video is a Library asset under Created videos."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        logger.warning("video export unavailable: %s", ffmpeg_unavailable_reason())
        raise HTTPException(status_code=503, detail="Video export isn't available right now.")
    project = await get_project(ctx.db, ctx.user_id, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Save the project before exporting")
    doc = project["doc"]
    if not doc.get("scenes"):
        raise HTTPException(status_code=400, detail="Add scenes first.")
    org_id = ctx.organization_id
    generation_id = str(uuid.uuid4())
    bind_user_id(ctx.user_id)
    width, height = timeline.output_size(body.ratio, body.resolution)
    crf = 19 if body.bitrate == "high" else 25
    mix = doc.get("audioMix") or {"voice": 1, "music": 0.5, "source": 1}
    name = body.name or f"{project['name']} · {datetime.now().strftime('%H:%M:%S')}"

    async def _progress(db, record_id: str, text: str):
        await broadcast_event(org_id, "processing", {
            "id": record_id, "generation_id": generation_id, "project_id": project_id,
            "status": "processing", "logs": [text],
        }, record_id, ctx.user_id)

    async def _bg():
        async with standalone_background_session(ctx) as db:
            item = await create_initial_item(
                db, organization_id=org_id, user_id=ctx.user_id, type="export", model_name="ffmpeg",
                prompt=project["name"], params={"ratio": body.ratio, "resolution": body.resolution},
                project_id=project_id, generation_id=generation_id,
            )
            record_id = item["id"]
            await broadcast_event(org_id, "started", item, record_id, ctx.user_id)
            try:
                ids = {s.get("assetId") for s in doc["scenes"]} | {doc.get("musicId"), doc.get("voiceId")}
                rows = {}
                for aid in ids:
                    if aid:
                        row = await get_asset_any_user(db, aid)
                        if row and row.trashed_at is None:
                            rows[aid] = {"type": row.type, "url": row.url, "mime": row.mime}
                specs = timeline.plan_scenes(doc, rows)
                with tempfile.TemporaryDirectory(prefix="pvs-export-") as tmp:
                    cache: Dict[str, str] = {}

                    async def local(asset: dict, aid: str) -> str:
                        if aid not in cache:
                            suffix = Path(asset["url"].split("?", 1)[0]).suffix or (".mp4" if asset["type"] == "video" else ".png")
                            path = str(Path(tmp) / f"src-{len(cache)}{suffix}")
                            Path(path).write_bytes(await fetch_media_bytes(asset["url"]))
                            cache[aid] = path
                        return cache[aid]

                    segments = []
                    for i, (scene, spec) in enumerate(zip(doc["scenes"], specs)):
                        await _progress(db, record_id, f"Rendering scene {i + 1}/{len(specs)}")
                        src = await local(spec["asset"], scene["assetId"])
                        out = str(Path(tmp) / f"seg-{i:03d}.mp4")
                        await anyio.to_thread.run_sync(functools.partial(
                            timeline.render_segment, ffmpeg, src, out, kind=spec["kind"], width=width, height=height,
                            duration=spec["duration"], source_in=spec["source_in"], scale=spec["scale"],
                            position=spec["position"], motion=spec["motion"], transition=spec["transition"],
                            source_volume=float(mix.get("source", 1)), crf=crf,
                        ))
                        segments.append(out)
                    await _progress(db, record_id, "Joining scenes")
                    joined = str(Path(tmp) / "joined.mp4")
                    await anyio.to_thread.run_sync(functools.partial(timeline.concat_segments, ffmpeg, segments, joined))
                    total = sum(s["duration"] for s in specs)
                    voice = await local(rows[doc["voiceId"]], doc["voiceId"]) if doc.get("voiceId") in rows else None
                    music = await local(rows[doc["musicId"]], doc["musicId"]) if doc.get("musicId") in rows else None
                    await _progress(db, record_id, "Mixing audio and captions")
                    final_path = str(Path(tmp) / "final.mp4")
                    await anyio.to_thread.run_sync(functools.partial(
                        timeline.mux_mix, ffmpeg, joined, final_path, total=total, voice=voice, music=music,
                        voice_volume=float(mix.get("voice", 1)), music_volume=float(mix.get("music", 0.5)),
                        captions=timeline.absolute_captions(doc) if body.burn_captions else None, crf=crf,
                    ))
                    data = Path(final_path).read_bytes()
                asset = await store_asset_bytes(
                    ctx, db, data, mime="video/mp4", name=name, folder="outputs/exports", source="export",
                    category="Videos", project_id=project_id, created_project_id=project_id, item_id=record_id,
                )
                await update_item_success(db, record_id, url=asset["src"])
                final = await set_item_fields(db, record_id, {"asset_id": asset["id"]})
                await broadcast_event(org_id, "completed", {**(final or {}), "asset": asset}, record_id, ctx.user_id)
            except Exception as e:  # noqa: BLE001
                logger.exception("export failed")
                mark_failed(str(e))
                failed = await update_item_failed(db, record_id, str(e))
                await broadcast_event(org_id, "failed", failed, record_id, ctx.user_id)

    asyncio.create_task(run_task("export", _bg(), project_id=project_id))
    return {"status": "pending", "generation_id": generation_id}
