"""Org-scoped CRUD helpers for Flow Video 2.0.

Every helper takes an AsyncSession (the request's `ctx.db`, or a background
session from `core.bg_session`) so RLS applies. Output dicts for projects,
assets and versions use the camelCase shape the browser's reference-UI types
expect (`lib/palette/model.ts`), with epoch-millisecond timestamps.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.core.config import PLUGIN_BASE
from backend.api.models import Asset, Item, Message, Preference, Project, Version

logger = logging.getLogger("pltt_video_studio_2.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _ms(value: Optional[datetime]) -> Optional[int]:
    return int(value.timestamp() * 1000) if value else None


# ---------------------------------------------------------------------------
# Items (generation jobs)
# ---------------------------------------------------------------------------

def item_to_dict(item: Item) -> dict:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "project_id": item.project_id,
        "scene_id": item.scene_id,
        "asset_id": item.asset_id,
        "generation_id": item.generation_id,
        "type": item.type,
        "model_name": item.model_name,
        "prompt": item.prompt,
        "status": item.status,
        "url": item.url,
        "thumbnail_url": item.thumbnail_url,
        "all_generated_urls": item.all_generated_urls,
        "image_references": item.image_references,
        "gen_params": item.gen_params,
        "aspect_ratio": item.aspect_ratio,
        "resolution": item.resolution,
        "duration": item.duration,
        "error_message": item.error_message,
        "group": item.group,
        "is_favourite": item.is_favourite,
        "midjourney_job_id": item.midjourney_job_id,
        "midjourney_action": item.midjourney_action,
        "source_doc_id": item.source_doc_id,
        "is_upscaled": item.is_upscaled,
        "upscaled_doc_id": item.upscaled_doc_id,
        "is_upscale_image": item.is_upscale_image,
        "url_before_upscale": item.url_before_upscale,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


async def create_initial_item(
    db: AsyncSession,
    *,
    organization_id: int,
    user_id: str,
    type: str,
    model_name: str,
    prompt: str,
    params: dict,
    project_id: Optional[str] = None,
    scene_id: Optional[str] = None,
    generation_id: Optional[str] = None,
) -> dict:
    item = Item(
        id=_new_id(),
        organization_id=organization_id,
        user_id=user_id,
        project_id=project_id,
        scene_id=scene_id,
        generation_id=generation_id or _new_id(),
        type=type,
        model_name=model_name,
        prompt=prompt,
        status="in_progress",
        aspect_ratio=params.get("aspect_ratio"),
        resolution=params.get("resolution"),
        duration=str(params["duration"]) if params.get("duration") is not None else None,
        image_references=params.get("image_references") or [],
        gen_params=dict(params) if params else {},
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item_to_dict(item)


async def update_item_success(
    db: AsyncSession, item_id: str, *, url: str, all_generated_urls: Optional[list] = None
) -> Optional[dict]:
    item = (await db.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
    if not item:
        return None
    item.status = "completed"
    item.url = url
    item.all_generated_urls = all_generated_urls or [{"provider": item.model_name, "url": url}]
    item.updated_at = _now()
    await db.commit()
    await db.refresh(item)
    return item_to_dict(item)


async def update_item_failed(db: AsyncSession, item_id: str, error: str) -> Optional[dict]:
    item = (await db.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
    if not item:
        return None
    item.status = "failed"
    item.error_message = str(error)[:4000]
    item.updated_at = _now()
    await db.commit()
    await db.refresh(item)
    return item_to_dict(item)


async def set_item_fields(db: AsyncSession, item_id: str, fields: dict) -> Optional[dict]:
    item = (await db.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
    if not item:
        return None
    for k, v in fields.items():
        if hasattr(item, k):
            setattr(item, k, v)
    item.updated_at = _now()
    await db.commit()
    await db.refresh(item)
    return item_to_dict(item)


async def locate_item(db: AsyncSession, user_id: str, item_id: str) -> Optional[dict]:
    res = await db.execute(select(Item).where(and_(Item.id == item_id, Item.user_id == user_id)))
    item = res.scalar_one_or_none()
    return item_to_dict(item) if item else None


async def get_items_by_generation_id(db: AsyncSession, user_id: str, generation_id: str) -> list[dict]:
    res = await db.execute(
        select(Item)
        .where(and_(Item.user_id == user_id, Item.generation_id == generation_id))
        .order_by(Item.created_at)
    )
    return [item_to_dict(i) for i in res.scalars().all()]


async def find_existing_action_item(
    db: AsyncSession, user_id: str, source_doc_id: str, action: str
) -> Optional[dict]:
    res = await db.execute(
        select(Item).where(
            and_(
                Item.user_id == user_id,
                Item.source_doc_id == source_doc_id,
                Item.midjourney_action == action,
                Item.status == "completed",
            )
        )
    )
    item = res.scalars().first()
    return item_to_dict(item) if item else None


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def project_to_out(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "doc": p.doc or {},
        "rev": p.rev,
        "thumbnail_url": p.thumbnail_url,
        "updated": _ms(p.updated_at) or _ms(p.created_at) or 0,
    }


async def list_projects(db: AsyncSession, user_id: str) -> list[dict]:
    res = await db.execute(
        select(Project)
        .where(and_(Project.user_id == user_id, Project.trashed_at.is_(None)))
        .order_by(desc(Project.updated_at))
    )
    return [project_to_out(p) for p in res.scalars().all()]


async def _project_row(db: AsyncSession, user_id: str, project_id: str) -> Optional[Project]:
    res = await db.execute(
        select(Project).where(and_(Project.id == project_id, Project.user_id == user_id))
    )
    return res.scalar_one_or_none()


async def get_project(db: AsyncSession, user_id: str, project_id: str) -> Optional[dict]:
    p = await _project_row(db, user_id, project_id)
    return project_to_out(p) if p and not p.trashed_at else None


async def upsert_project(
    db: AsyncSession,
    *,
    organization_id: int,
    user_id: str,
    project_id: str,
    name: str,
    doc: dict,
    thumbnail_url: Optional[str] = None,
    base_rev: Optional[int] = None,
) -> dict:
    p = await _project_row(db, user_id, project_id)
    if p is None:
        p = Project(
            id=project_id, organization_id=organization_id, user_id=user_id,
            name=name[:255], doc=doc, rev=1, thumbnail_url=thumbnail_url,
        )
        db.add(p)
    else:
        if base_rev is not None and base_rev != p.rev:
            # Last write wins (the browser is the single writer); log so a
            # two-tab overwrite is diagnosable.
            logger.info("project %s saved from stale rev %s (current %s)", project_id, base_rev, p.rev)
        p.name = name[:255]
        p.doc = doc
        p.rev = (p.rev or 0) + 1
        p.trashed_at = None
        if thumbnail_url is not None:
            p.thumbnail_url = thumbnail_url
        p.updated_at = _now()
    await db.commit()
    await db.refresh(p)
    return project_to_out(p)


async def rename_project(db: AsyncSession, user_id: str, project_id: str, name: str) -> Optional[dict]:
    p = await _project_row(db, user_id, project_id)
    if not p:
        return None
    p.name = name[:255]
    p.updated_at = _now()
    await db.commit()
    await db.refresh(p)
    return project_to_out(p)


async def trash_project(db: AsyncSession, user_id: str, project_id: str) -> bool:
    p = await _project_row(db, user_id, project_id)
    if not p:
        return False
    p.trashed_at = _now()
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def _browser_url(url: Optional[str], asset_id: str, poster: bool = False) -> Optional[str]:
    """Dev storage hands out file:// URLs a page can't load; route those
    through the plugin's `/media/{id}` endpoint instead."""
    if not url or not url.startswith("file://"):
        return url
    return f"{PLUGIN_BASE}/media/{asset_id}" + ("?poster=1" if poster else "")


def asset_to_out(a: Asset) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "type": a.type,
        "src": _browser_url(a.url, a.id),
        "poster": _browser_url(a.poster_url, a.id, poster=True),
        "duration": a.duration,
        "width": a.width,
        "height": a.height,
        "size": a.size_bytes,
        "category": a.category,
        "uploaded": bool(a.uploaded),
        "source": a.source,
        "projectId": a.project_id,
        "createdProjectId": a.created_project_id,
        "createdAt": _ms(a.created_at),
        "archivedAt": _ms(a.archived_at),
        "trashed": a.trashed_at is not None,
        "analysis": a.analysis,
    }


async def create_asset(db: AsyncSession, *, organization_id: int, user_id: str, **fields: Any) -> dict:
    a = Asset(id=fields.pop("id", None) or _new_id(), organization_id=organization_id, user_id=user_id)
    for k, v in fields.items():
        if hasattr(a, k):
            setattr(a, k, v)
    if not a.name:
        a.name = "Untitled"
    a.name = a.name[:255]
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return asset_to_out(a)


async def _asset_row(db: AsyncSession, user_id: str, asset_id: str) -> Optional[Asset]:
    res = await db.execute(select(Asset).where(and_(Asset.id == asset_id, Asset.user_id == user_id)))
    return res.scalar_one_or_none()


async def get_asset(db: AsyncSession, user_id: str, asset_id: str) -> Optional[dict]:
    a = await _asset_row(db, user_id, asset_id)
    return asset_to_out(a) if a else None


async def get_asset_any_user(db: AsyncSession, asset_id: str) -> Optional[Asset]:
    """RLS still scopes to the org; used by the dev media endpoint."""
    return (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()


async def find_asset_by_hash(db: AsyncSession, user_id: str, content_hash: str) -> Optional[dict]:
    res = await db.execute(
        select(Asset).where(
            and_(Asset.user_id == user_id, Asset.content_hash == content_hash, Asset.trashed_at.is_(None))
        )
    )
    a = res.scalars().first()
    return asset_to_out(a) if a else None


async def list_assets(db: AsyncSession, user_id: str, include_trashed: bool = True) -> list[dict]:
    q = select(Asset).where(Asset.user_id == user_id)
    if not include_trashed:
        q = q.where(Asset.trashed_at.is_(None))
    res = await db.execute(q.order_by(Asset.created_at))
    return [asset_to_out(a) for a in res.scalars().all()]


async def list_assets_by_item(db: AsyncSession, user_id: str, item_ids: list[str]) -> list[dict]:
    if not item_ids:
        return []
    res = await db.execute(
        select(Asset).where(and_(Asset.user_id == user_id, Asset.item_id.in_(item_ids)))
    )
    return [asset_to_out(a) for a in res.scalars().all()]


ASSET_CATEGORIES = {"Images", "Videos", "Characters", "Scenes", "Locations", "Voices & audio"}


async def patch_asset(db: AsyncSession, user_id: str, asset_id: str, patch: dict) -> Optional[dict]:
    a = await _asset_row(db, user_id, asset_id)
    if not a:
        return None
    if patch.get("name"):
        a.name = str(patch["name"])[:255]
    if patch.get("category") in ASSET_CATEGORIES:
        a.category = patch["category"]
    if "trashed" in patch and patch["trashed"] is not None:
        a.trashed_at = _now() if patch["trashed"] else None
    if "archived" in patch and patch["archived"] is not None:
        a.archived_at = _now() if patch["archived"] else None
    if "analysis" in patch:
        a.analysis = patch["analysis"]
    if "project_id" in patch and patch["project_id"]:
        a.project_id = patch["project_id"]
    a.updated_at = _now()
    await db.commit()
    await db.refresh(a)
    return asset_to_out(a)


async def delete_asset(db: AsyncSession, user_id: str, asset_id: str) -> bool:
    a = await _asset_row(db, user_id, asset_id)
    if not a:
        return False
    await db.delete(a)
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Versions (Archive snapshots)
# ---------------------------------------------------------------------------

def version_to_out(v: Version) -> dict:
    return {"id": v.id, "name": v.name, "date": _ms(v.created_at), "project": v.snapshot}


async def list_versions(db: AsyncSession, user_id: str) -> list[dict]:
    res = await db.execute(
        select(Version).where(Version.user_id == user_id).order_by(desc(Version.created_at))
    )
    return [version_to_out(v) for v in res.scalars().all()]


async def create_version(
    db: AsyncSession, *, organization_id: int, user_id: str, version_id: Optional[str],
    project_id: str, name: str, snapshot: dict,
) -> dict:
    if version_id:
        existing = (
            await db.execute(select(Version).where(and_(Version.id == version_id, Version.user_id == user_id)))
        ).scalar_one_or_none()
        if existing:
            return version_to_out(existing)
    v = Version(
        id=version_id or _new_id(), organization_id=organization_id, user_id=user_id,
        project_id=project_id, name=name[:255], snapshot=snapshot,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return version_to_out(v)


async def delete_version(db: AsyncSession, user_id: str, version_id: str) -> bool:
    v = (
        await db.execute(select(Version).where(and_(Version.id == version_id, Version.user_id == user_id)))
    ).scalar_one_or_none()
    if not v:
        return False
    await db.delete(v)
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Chat messages
# ---------------------------------------------------------------------------

def message_to_out(m: Message) -> dict:
    out: dict = {"id": m.id, "role": m.role, "text": m.text, "createdAt": _ms(m.created_at)}
    if m.generation:
        out["generation"] = m.generation
    if m.actions:
        out["actions"] = m.actions
    return out


async def add_message(
    db: AsyncSession, *, organization_id: int, user_id: str, project_id: str, role: str,
    text: str, generation: Optional[dict] = None, actions: Optional[list] = None,
    message_id: Optional[str] = None,
) -> dict:
    m = Message(
        id=message_id or _new_id(), organization_id=organization_id, user_id=user_id,
        project_id=project_id, role=role, text=text, generation=generation, actions=actions,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return message_to_out(m)


async def list_messages(db: AsyncSession, user_id: str, project_id: str, limit: int = 40) -> list[dict]:
    res = await db.execute(
        select(Message)
        .where(and_(Message.user_id == user_id, Message.project_id == project_id))
        .order_by(desc(Message.created_at))
        .limit(limit)
    )
    return [message_to_out(m) for m in reversed(res.scalars().all())]


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

async def get_preferences(db: AsyncSession, user_id: str) -> dict:
    p = (await db.execute(select(Preference).where(Preference.user_id == user_id))).scalar_one_or_none()
    return dict(p.data or {}) if p else {}


async def set_preferences(db: AsyncSession, *, organization_id: int, user_id: str, data: dict) -> dict:
    p = (await db.execute(select(Preference).where(Preference.user_id == user_id))).scalar_one_or_none()
    if p is None:
        p = Preference(id=_new_id(), organization_id=organization_id, user_id=user_id, data=data)
        db.add(p)
    else:
        p.data = {**(p.data or {}), **data}
        p.updated_at = _now()
    await db.commit()
    return dict(p.data or {})
