"""Initial schema for Flow Video 2.0.

Creates projects / assets / items / versions / messages / preferences and
enables the org-isolation row-level-security policy on each.

Revision ID: 001_init
Revises:
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from palette_sdk.db import ensure_org_rls


revision = "001_init"
down_revision = None
branch_labels = None
depends_on = None

P = "pltt_video_studio_2__"


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
    ]


def _ts(updated: bool = True) -> list[sa.Column]:
    cols = [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now())]
    if updated:
        cols.append(sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    return cols


def upgrade() -> None:
    op.create_table(
        f"{P}projects",
        *_common(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("doc", sa.JSON(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
    )
    ensure_org_rls(op, f"{P}projects")

    op.create_table(
        f"{P}assets",
        *_common(),
        sa.Column("project_id", sa.String(64), nullable=True, index=True),
        sa.Column("created_project_id", sa.String(64), nullable=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("poster_url", sa.Text(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("mime", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False, server_default="Images"),
        sa.Column("source", sa.String(16), nullable=False, server_default="upload"),
        sa.Column("uploaded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_hash", sa.String(64), nullable=True, index=True),
        sa.Column("analysis", sa.JSON(), nullable=True),
        sa.Column("item_id", sa.String(64), nullable=True, index=True),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
    )
    ensure_org_rls(op, f"{P}assets")

    op.create_table(
        f"{P}items",
        *_common(),
        sa.Column("project_id", sa.String(64), nullable=True, index=True),
        sa.Column("scene_id", sa.String(64), nullable=True, index=True),
        sa.Column("asset_id", sa.String(64), nullable=True),
        sa.Column("generation_id", sa.String(64), nullable=True, index=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_progress"),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("all_generated_urls", sa.JSON(), nullable=True),
        sa.Column("image_references", sa.JSON(), nullable=True),
        sa.Column("gen_params", sa.JSON(), nullable=True),
        sa.Column("aspect_ratio", sa.String(16), nullable=True),
        sa.Column("resolution", sa.String(16), nullable=True),
        sa.Column("duration", sa.String(16), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("group", sa.String(16), nullable=True),
        sa.Column("is_favourite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_sample", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("midjourney_job_id", sa.String(128), nullable=True),
        sa.Column("midjourney_action", sa.String(8), nullable=True),
        sa.Column("source_doc_id", sa.String(64), nullable=True, index=True),
        sa.Column("is_upscaled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("upscaled_doc_id", sa.String(64), nullable=True),
        sa.Column("is_upscale_image", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("url_before_upscale", sa.Text(), nullable=True),
        *_ts(),
    )
    ensure_org_rls(op, f"{P}items")

    op.create_table(
        f"{P}versions",
        *_common(),
        sa.Column("project_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        *_ts(updated=False),
    )
    ensure_org_rls(op, f"{P}versions")

    op.create_table(
        f"{P}messages",
        *_common(),
        sa.Column("project_id", sa.String(64), nullable=False, index=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("generation", sa.JSON(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        *_ts(updated=False),
    )
    ensure_org_rls(op, f"{P}messages")

    op.create_table(
        f"{P}preferences",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), nullable=False, unique=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    ensure_org_rls(op, f"{P}preferences")


def downgrade() -> None:
    for table in ("preferences", "messages", "versions", "items", "assets", "projects"):
        op.drop_table(f"{P}{table}")
