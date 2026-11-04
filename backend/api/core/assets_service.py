"""Turning bytes / provider outputs into Library asset rows.

Shared by uploads, generation, TTS/music and exports so every asset gets
the same storage layout, poster frame and duration.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import PurePosixPath
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.core.media_probe import probe_bytes
from backend.api.core.storage_helper import fetch_media_bytes, upload_bytes_to_storage
from backend.api.db_helpers import create_asset

logger = logging.getLogger("pltt_video_studio_2.assets")

DEFAULT_CATEGORY = {"image": "Images", "video": "Videos", "audio": "Voices & audio"}


def kind_for_mime(mime: str) -> Optional[str]:
    top = (mime or "").split("/", 1)[0]
    return top if top in ("image", "video", "audio") else None


def suffix_for(name: str, mime: str) -> str:
    suffix = PurePosixPath(name or "").suffix.lower()
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension((mime or "").split(";")[0].strip()) or ""
    return ".jpg" if guessed == ".jpe" else guessed


async def store_asset_bytes(
    ctx: Any,
    db: AsyncSession,
    data: bytes,
    *,
    mime: str,
    name: str,
    folder: str,
    source: str,
    category: Optional[str] = None,
    project_id: Optional[str] = None,
    created_project_id: Optional[str] = None,
    item_id: Optional[str] = None,
    content_hash: Optional[str] = None,
    storage_key: Optional[str] = None,
    analysis: Optional[dict] = None,
    uploaded: bool = False,
) -> dict:
    kind = kind_for_mime(mime)
    if not kind:
        raise ValueError(f"unsupported media type {mime!r}")
    suffix = suffix_for(name, mime)
    url = await upload_bytes_to_storage(ctx, data, mime, folder=folder, ext=suffix, key=storage_key)
    meta = await probe_bytes(data, suffix, kind)
    poster_url = None
    if meta.get("poster"):
        try:
            poster_url = await upload_bytes_to_storage(ctx, meta["poster"], "image/jpeg", folder="posters", ext=".jpg")
        except Exception as exc:
            logger.warning("poster upload failed: %s", exc)
    return await create_asset(
        db,
        organization_id=ctx.organization_id,
        user_id=ctx.user_id,
        name=name,
        type=kind,
        url=url,
        poster_url=poster_url,
        duration=meta.get("duration"),
        width=meta.get("width"),
        height=meta.get("height"),
        mime=mime,
        size_bytes=len(data),
        category=category or DEFAULT_CATEGORY[kind],
        source=source,
        uploaded=uploaded,
        project_id=project_id,
        created_project_id=created_project_id,
        item_id=item_id,
        content_hash=content_hash,
        analysis=analysis,
    )


async def store_asset_from_url(
    ctx: Any,
    db: AsyncSession,
    url: str,
    *,
    kind: str,
    name: str,
    source: str,
    category: Optional[str] = None,
    project_id: Optional[str] = None,
    item_id: Optional[str] = None,
) -> dict:
    """Copy a provider output (CDN URL / data URL) into app storage and
    register it as an asset."""
    data = await fetch_media_bytes(url)
    mime = {"image": "image/png", "video": "video/mp4", "audio": "audio/mpeg"}[kind]
    sniffed = mimetypes.guess_type(PurePosixPath(url.split("?", 1)[0]).name)[0]
    if sniffed and sniffed.startswith(kind + "/"):
        mime = sniffed
    folder = {"image": "outputs/images", "video": "outputs/videos", "audio": "outputs/audio"}[kind]
    return await store_asset_bytes(
        ctx, db, data, mime=mime, name=name, folder=folder, source=source,
        category=category, project_id=project_id, item_id=item_id,
    )
