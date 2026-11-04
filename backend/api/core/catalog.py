"""Generation model catalog served by `GET /models`.

Capabilities come from the videos app's `video-helper.tsx` model options and
the storyboard `fal_helper` registry; availability depends on which provider
keys this install has configured, and on the platform's model catalogs
(`ctx.llm` / `ctx.media`, see `platform_models`).
"""

from __future__ import annotations

import os
from typing import Any

from backend.api.core import fal_helper, llm, platform_models
from backend.api.core.secrets import read_secret
from backend.api.core.video_merge import find_ffmpeg

_KLING_DURATIONS = list(range(3, 16))
# fal's Kling 3.0 / O3 endpoints take no resolution and only these ratios.
_KLING_RATIOS = ["16:9", "9:16", "1:1"]
_SEEDANCE_RATIOS = ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]

# `resolutions` mirrors each fal endpoint's `resolution` enum; "auto" means the
# endpoint has no resolution parameter, so none is sent.
# Newest first: this order is the video model list in the composer.
VIDEO_MODELS: list[dict[str, Any]] = [
    {"id": "kling_4_0", "label": "Kling 4.0", "detail": "Up to 30s, 1080p, native audio",
     "durations": list(range(3, 31)), "resolutions": ["720p", "1080p"],
     "aspect_ratios": ["16:9", "9:16", "1:1", "21:9"],
     # its fal endpoint has no generate_audio switch: sound is always on
     "audio": True, "audio_fixed": True, "end_frame": True, "reference_images": False},
    {"id": "seedance_2_5", "label": "Seedance 2.5", "detail": "Reference images, up to 30s",
     "durations": list(range(4, 31)), "resolutions": ["480p", "720p", "1080p"],
     "aspect_ratios": _SEEDANCE_RATIOS,
     "audio": True, "end_frame": True, "reference_images": True},
    {"id": "kling_o3_pro", "label": "Kling O3 Pro", "detail": "Multi-prompt",
     "durations": _KLING_DURATIONS, "resolutions": ["auto"], "aspect_ratios": _KLING_RATIOS,
     "audio": True, "end_frame": True, "reference_images": False},
    {"id": "kling_3_0_pro", "label": "Kling 3.0 Pro", "detail": "Keyframes, elements",
     "durations": _KLING_DURATIONS, "resolutions": ["auto"], "aspect_ratios": _KLING_RATIOS,
     "audio": True, "end_frame": True, "reference_images": False},
    {"id": "kling_3_0_standard", "label": "Palette Video", "detail": "Kling 3.0 Standard",
     "durations": _KLING_DURATIONS, "resolutions": ["auto"], "aspect_ratios": _KLING_RATIOS,
     "audio": True, "end_frame": True, "reference_images": False},
    {"id": "seedance_2_0", "label": "Seedance 2.0", "detail": "Reference images, up to 4K",
     "durations": list(range(4, 16)), "resolutions": ["480p", "720p", "1080p", "4k"],
     "aspect_ratios": _SEEDANCE_RATIOS,
     "audio": True, "end_frame": True, "reference_images": True},
    {"id": "ltx_video", "label": "LTX Video", "detail": "Fast drafts",
     "durations": [6, 8, 10], "resolutions": ["auto"], "aspect_ratios": _KLING_RATIOS,
     "audio": False, "end_frame": False, "reference_images": False, "dev_only": True},
]
DEFAULT_VIDEO_MODEL = "kling_3_0_standard"

IMAGE_MODELS: list[dict[str, Any]] = [
    {"id": "auto", "label": "Auto", "resolves_to": "nano_banana_pro"},
    {"id": "nano_banana_pro", "label": "Nano Banana Pro"},
    # Served by the platform and fal only: the router and Runware have no verified 2.5 alias yet.
    {"id": "openai/gpt-image-2.5", "label": "GPT Image 2.5", "providers": ["fal"]},
    {"id": "midjourney", "label": "Midjourney", "requires": "MIDJOURNEY_KEY"},
    {"id": "flux_schnell", "label": "Flux Schnell"},
]


def video_model(model_id: str) -> dict[str, Any]:
    # Unknown ids fall back to the default model, not the first (newest) entry.
    return next((m for m in VIDEO_MODELS if m["id"] == model_id), next(m for m in VIDEO_MODELS if m["id"] == DEFAULT_VIDEO_MODEL))


def fit_aspect_ratio(model_id: str, ratio: str | None) -> str:
    """The model's supported aspect ratio closest to `ratio` (16:9 if unparseable)."""
    options = video_model(model_id)["aspect_ratios"]
    ratio = ratio or "16:9"
    if ratio in options:
        return ratio

    def value(r: str) -> float:
        w, h = r.split(":")
        return float(w) / float(h)

    try:
        target = value(ratio)
    except (ValueError, ZeroDivisionError):
        return "16:9" if "16:9" in options else options[0]
    return min(options, key=lambda r: abs(value(r) - target))


def clamp_duration(model_id: str, seconds: float) -> int:
    options = video_model(model_id)["durations"]
    target = round(float(seconds or options[0]))
    return min(options, key=lambda d: (abs(d - target), -d))


# Saved settings that still name a replaced model.
_IMAGE_MODEL_ALIASES = {"openai/gpt-image-2": "openai/gpt-image-2.5"}


def resolve_image_model(model_id: str | None) -> str:
    if not model_id or model_id == "auto":
        return "nano_banana_pro"
    model_id = _IMAGE_MODEL_ALIASES.get(model_id, model_id)
    return model_id if any(m["id"] == model_id for m in IMAGE_MODELS) else "nano_banana_pro"


def _router_enabled(ctx: Any) -> bool:
    return (read_secret(ctx, "LLM_ROUTER_ENABLED") or "").lower() in ("1", "true", "yes", "on") and bool(
        read_secret(ctx, "LLM_ROUTER_API_KEY")
    )


def _platform_video(ctx: Any, model_id: str) -> bool:
    """The platform's media catalog (cached) lists this model's text- or image-to-video endpoint."""
    ids = (fal_helper._VIDEO_MODELS.get(model_id), fal_helper._VIDEO_I2V_MODELS.get(model_id))
    return any(i and platform_models.media_listed(ctx, i) for i in ids)


def _platform_image(ctx: Any, model_id: str) -> bool:
    if model_id == "midjourney":
        return False
    fal_id = fal_helper._IMAGE_MODELS.get(resolve_image_model(model_id))
    return bool(fal_id) and (platform_models.media_listed(ctx, fal_id)
                             or platform_models.media_listed(ctx, f"{fal_id}/edit"))


def catalog(ctx: Any) -> dict[str, Any]:
    """Availability per model. Platform entries read the cached catalogs — `await platform_models.warm(ctx)`
    first (the /models and /bootstrap routes do)."""
    fal = bool(read_secret(ctx, "FAL_KEY"))
    runware = bool(read_secret(ctx, "RUNWARE_KEY"))
    router = _router_enabled(ctx)
    dev = bool(os.environ.get("PALETTE_DEV") or os.environ.get("PALETTE_DEV_DATABASE_URL"))
    video = [
        {**m, "available": fal or runware or _platform_video(ctx, m["id"])}
        for m in VIDEO_MODELS
        if not m.get("dev_only") or dev
    ]
    image = []
    for m in IMAGE_MODELS:
        if m["id"] == "midjourney":
            ok = bool(read_secret(ctx, "MIDJOURNEY_KEY")) or router
        elif m.get("providers") == ["fal"]:
            ok = fal or _platform_image(ctx, m["id"])
        else:
            ok = fal or runware or router or _platform_image(ctx, m["id"])
        image.append({**m, "available": ok})
    eleven = bool(read_secret(ctx, "ELEVEN_API_KEY"))
    platform_tts = platform_models.media_listed(ctx, platform_models.PLATFORM_TTS)
    return {
        "text": llm.text_models(ctx),
        "default_text": llm.default_text_model(ctx),
        "video": video,
        "default_video": DEFAULT_VIDEO_MODEL,
        "image": image,
        "audio": {"tts": eleven or platform_tts, "music": eleven},
        "ffmpeg": find_ffmpeg() is not None,
        "providers": {"fal": fal, "runware": runware, "router": router, "elevenlabs": eleven,
                      "platform_media": platform_models.media_service(ctx) is not None},
    }
