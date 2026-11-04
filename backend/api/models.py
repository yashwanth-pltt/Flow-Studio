"""SQLAlchemy ORM models for the Flow Video 2.0 plugin.

All tables inherit OrgScopedTable so Palette scopes rows to the current
organization and enables Postgres RLS. Table names carry the
`pltt_video_studio_2__` prefix per the plugin schema convention.

Ownership split: the browser is the only writer of `Project.doc` (scenes,
captions, audio mix, generation settings…); the server writes assets, items,
messages and versions. That keeps debounced autosave and background jobs from
overwriting each other.

Import-alias note: the Palette CLI's local dev runner pre-imports this file
as the flat top-level name `models`, while the rest of the code imports it as
`backend.api.models`. Registering both names against one module object keeps
SQLAlchemy from defining every table twice on the shared metadata.
"""

from __future__ import annotations

import sys as _sys

_THIS = _sys.modules[__name__]
_ALIASES = ("models", "backend.api.models")

_other = next(
    (
        _sys.modules[_n]
        for _n in _ALIASES
        if _n in _sys.modules and _sys.modules[_n] is not _THIS
    ),
    None,
)

if _other is not None:
    globals().update(
        {_k: _v for _k, _v in _other.__dict__.items() if not _k.startswith("__")}
    )
    _sys.modules[__name__] = _other
else:
    for _name in _ALIASES:
        _sys.modules.setdefault(_name, _THIS)

    import uuid
    from datetime import datetime
    from typing import Any, Optional

    from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Integer, String, Text, func
    from sqlalchemy.orm import Mapped, mapped_column

    from palette_sdk import OrgScopedTable

    PREFIX = "pltt_video_studio_2__"

    def _uuid() -> str:
        return str(uuid.uuid4())

    class Project(OrgScopedTable):
        __tablename__ = f"{PREFIX}projects"

        # Client-generated UUID so the browser can create projects offline-first.
        id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
        user_id: Mapped[str] = mapped_column(String(64), index=True)
        name: Mapped[str] = mapped_column(String(255))
        doc: Mapped[Any] = mapped_column(JSON)
        rev: Mapped[int] = mapped_column(Integer, default=0)
        thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        trashed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
        updated_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
        )

    class Asset(OrgScopedTable):
        """Library media: uploads, recordings, generated stills/clips, voice,
        music and exported videos."""

        __tablename__ = f"{PREFIX}assets"

        id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
        user_id: Mapped[str] = mapped_column(String(64), index=True)
        # Project the media was made in or added from (null for plain library uploads).
        project_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
        # Set only on final exports — these show up under "Created videos".
        created_project_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
        name: Mapped[str] = mapped_column(String(255))
        type: Mapped[str] = mapped_column(String(16))  # image | video | audio
        url: Mapped[str] = mapped_column(Text)
        poster_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
        width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
        height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
        mime: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
        size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
        category: Mapped[str] = mapped_column(String(32), default="Images")
        # upload | recording | generated | camera | tts | music | export
        source: Mapped[str] = mapped_column(String(16), default="upload")
        uploaded: Mapped[bool] = mapped_column(Boolean, default=False)
        content_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
        analysis: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
        item_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
        trashed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
        archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
        updated_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
        )

    class Item(OrgScopedTable):
        """One generation job result (image, video, plan, export…). Columns
        mirror the storyboard app so its provider code ports unchanged."""

        __tablename__ = f"{PREFIX}items"

        id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
        user_id: Mapped[str] = mapped_column(String(64), index=True)
        project_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
        scene_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
        asset_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        generation_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
        type: Mapped[str] = mapped_column(String(16))
        model_name: Mapped[str] = mapped_column(String(64))
        prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        status: Mapped[str] = mapped_column(String(32), default="in_progress")
        url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        all_generated_urls: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
        image_references: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
        gen_params: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
        aspect_ratio: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
        resolution: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
        duration: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
        error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        group: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
        is_favourite: Mapped[bool] = mapped_column(Boolean, default=False)
        is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
        midjourney_job_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
        midjourney_action: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
        source_doc_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
        is_upscaled: Mapped[bool] = mapped_column(Boolean, default=False)
        upscaled_doc_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
        is_upscale_image: Mapped[bool] = mapped_column(Boolean, default=False)
        url_before_upscale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
        updated_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
        )

    class Version(OrgScopedTable):
        """Archive snapshot of a project doc. No FK: versions of a deleted
        project can still be restored or branched."""

        __tablename__ = f"{PREFIX}versions"

        id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
        user_id: Mapped[str] = mapped_column(String(64), index=True)
        project_id: Mapped[str] = mapped_column(String(64), index=True)
        name: Mapped[str] = mapped_column(String(255))
        snapshot: Mapped[Any] = mapped_column(JSON)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    class Message(OrgScopedTable):
        """Project chat history (user instructions and agent replies)."""

        __tablename__ = f"{PREFIX}messages"

        id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
        user_id: Mapped[str] = mapped_column(String(64), index=True)
        project_id: Mapped[str] = mapped_column(String(64), index=True)
        role: Mapped[str] = mapped_column(String(16))
        text: Mapped[str] = mapped_column(Text)
        generation: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
        actions: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
        status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    class Preference(OrgScopedTable):
        """Per-user settings (landing generation settings, default text model)."""

        __tablename__ = f"{PREFIX}preferences"

        id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
        user_id: Mapped[str] = mapped_column(String(64), index=True, unique=True)
        data: Mapped[Any] = mapped_column(JSON)
        updated_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
        )
