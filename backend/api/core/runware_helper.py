"""Runware fallback generation helpers, ported from pltt-agg.

API keys are passed in by the caller (read from `ctx.secret("RUNWARE_KEY")`).
"""

from __future__ import annotations

import math
import logging
from typing import List, Optional, Tuple

from backend.api.core.std_logs import call, size

logger = logging.getLogger("pltt_video_studio_2.runware")

RUNWARE_FALLBACK_MAP = {
    "nano_banana_pro": "google:4@2",
    "flux_schnell": "runware:100@1",
}

RUNWARE_VIDEO_FALLBACK_MAP = {
    "kling_3_0_pro": "klingai:kling-video@3-pro",
    "kling_o3_pro": "klingai:kling-video@o3-pro",
    "kling_4_0": "klingai:kling-video@4.0",
    "seedance_2_0": "bytedance:seedance@2.0-fast",
    "seedance_2_5": "bytedance:seedance@2.5",
    "ltx_video": "lightricks:ltx@2.3-fast",
}

_VIDEO_DIMENSIONS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (768, 768),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
    "21:9": (1280, 548),
}

_RUNWARE_MODEL_DIMENSION_OVERRIDES: dict = {
    "lightricks:ltx@2.3-fast": {
        "16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1920, 1080),
        "4:3": (1920, 1440), "3:4": (1440, 1920), "21:9": (2560, 1080),
    },
    # Kling 3 Pro / o3 Pro only accept these exact sizes; anything else is
    # rejected with `unsupportedDimensions`. Unsupported ratios (4:3, 3:4, 21:9)
    # snap to the nearest allowed size.
    "klingai:kling-video@3-pro": {
        "16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1440, 1440),
        "4:3": (1440, 1440), "3:4": (1440, 1440), "21:9": (1920, 1080),
    },
    "klingai:kling-video@o3-pro": {
        "16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1440, 1440),
        "4:3": (1440, 1440), "3:4": (1440, 1440), "21:9": (1920, 1080),
    },
}

# Models whose sizes depend on the requested resolution (Runware's preset lists):
# {air: {resolution: {aspect_ratio: (w, h)}}}. A ratio the model lacks snaps to
# the nearest one it has; an unknown resolution uses the first listed.
_RUNWARE_RESOLUTION_DIMENSIONS: dict = {
    "klingai:kling-video@4.0": {
        "720p": {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (940, 940), "21:9": (1460, 624),
                 "4:3": (940, 940), "3:4": (940, 940)},
        "1080p": {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1440, 1440), "21:9": (2200, 940),
                  "4:3": (1440, 1440), "3:4": (1440, 1440)},
    },
    "bytedance:seedance@2.5": {
        "480p": {"16:9": (854, 480), "4:3": (752, 560), "1:1": (640, 640), "3:4": (560, 752),
                 "9:16": (480, 854), "21:9": (992, 432)},
        "720p": {"16:9": (1280, 720), "4:3": (1112, 834), "1:1": (960, 960), "3:4": (834, 1112),
                 "9:16": (720, 1280), "21:9": (1470, 630)},
        "1080p": {"16:9": (1920, 1080), "4:3": (1664, 1248), "1:1": (1440, 1440), "3:4": (1248, 1664),
                  "9:16": (1080, 1920), "21:9": (2206, 946)},
    },
}

# Models that take Runware's `settings.audio` switch for their native soundtrack.
_RUNWARE_AUDIO_SETTING = {"bytedance:seedance@2.5"}

_RUNWARE_VIDEO_DURATION_CONSTRAINTS: dict = {
    "klingai:kling-video@4.0": {"allowed": [float(s) for s in range(3, 31)], "default": 5.0},
    "bytedance:seedance@2.5": {"allowed": [float(s) for s in range(4, 31)], "default": 5.0},
    "lightricks:ltx@2.3-fast": {"allowed": [6.0, 8.0, 10.0], "default": 6.0},
    "klingai:kling-video@3-pro": {
        "allowed": [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
        "default": 5.0,
    },
    "klingai:kling-video@o3-pro": {
        "allowed": [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
        "default": 5.0,
    },
    # Seedance accepts any integer 4–15 seconds.
    "bytedance:seedance@2.0-fast": {
        "allowed": [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
        "default": 5.0,
    },
}

# Runware image inference constraints: each side must be a multiple of 64 and
# the total pixel count must fall within [MIN, MAX]. A naive width*ratio for
# non-square ratios (e.g. 9:16 at 1K → 576x1024 = 589,824) falls below MIN and
# is rejected; 4K landscape overshoots MAX — so we size to a target megapixel
# count instead and snap to the constraints.
_RUNWARE_MIN_PIXELS = 655_360
_RUNWARE_MAX_PIXELS = 8_294_400

# Some image models only accept an enumerated set of dimensions per
# (aspect_ratio, resolution); the generic megapixel sizing is rejected with
# `unsupportedDimensions`. Map those models to their exact allowed sizes.
# google:4@2 == nano_banana_pro.
_RUNWARE_IMAGE_MODEL_DIMENSIONS: dict = {
    "google:4@2": {
        "1:1":  {"1K": (1024, 1024), "2K": (2048, 2048), "4K": (4096, 4096)},
        "16:9": {"1K": (1376, 768),  "2K": (2752, 1536), "4K": (5504, 3072)},
        "9:16": {"1K": (768, 1376),  "2K": (1536, 2752), "4K": (3072, 5504)},
        "4:3":  {"1K": (1200, 896),  "2K": (2400, 1792), "4K": (4800, 3584)},
        "3:4":  {"1K": (896, 1200),  "2K": (1792, 2400), "4K": (3584, 4800)},
        "21:9": {"1K": (1548, 672),  "2K": (3168, 1344), "4K": (6336, 2688)},
    },
}


def get_runware_model(fal_model_name: str) -> Optional[str]:
    return RUNWARE_FALLBACK_MAP.get(fal_model_name)


def get_runware_video_model(fal_model_name: str) -> Optional[str]:
    return RUNWARE_VIDEO_FALLBACK_MAP.get(fal_model_name)


def aspect_ratio_to_dimensions(aspect_ratio: str, resolution: str = "1K", model: Optional[str] = None) -> Tuple[int, int]:
    """Converts an aspect ratio + resolution label to (width, height) for Runware.

    When `model` only accepts an enumerated set of dimensions, the exact allowed
    size for the (aspect_ratio, resolution) pair is returned; otherwise the
    dimensions are sized to a target megapixel count for the resolution.
    """
    # Model-specific exact dimensions (e.g. nano_banana_pro / google:4@2).
    model_dims = _RUNWARE_IMAGE_MODEL_DIMENSIONS.get(model or "")
    if model_dims:
        by_res = model_dims.get(aspect_ratio) or model_dims.get("1:1")
        if by_res:
            return by_res.get(resolution) or by_res.get("1K") or next(iter(by_res.values()))

    ratio_map = {
        "1:1": 1.0,
        "16:9": 16 / 9,
        "9:16": 9 / 16,
        "4:3": 4 / 3,
        "3:4": 3 / 4,
        "21:9": 21 / 9,
    }
    ratio = ratio_map.get(aspect_ratio, 1.0)

    target_area = {"1K": 1024 * 1024, "2K": 2048 * 2048, "4K": _RUNWARE_MAX_PIXELS}.get(
        resolution, 1024 * 1024
    )
    target_area = max(_RUNWARE_MIN_PIXELS, min(_RUNWARE_MAX_PIXELS, target_area))

    def snap(v: float) -> int:
        return max(64, int(round(v / 64)) * 64)

    width = snap(math.sqrt(target_area * ratio))
    height = snap(math.sqrt(target_area / ratio))

    while width * height > _RUNWARE_MAX_PIXELS:
        if width >= height:
            width -= 64
        else:
            height -= 64
    while width * height < _RUNWARE_MIN_PIXELS:
        if width <= height:
            width += 64
        else:
            height += 64

    return width, height


def aspect_ratio_to_video_dimensions(aspect_ratio: str, model: Optional[str] = None,
                                     resolution: Optional[str] = None) -> Tuple[int, int]:
    by_res = _RUNWARE_RESOLUTION_DIMENSIONS.get(model or "")
    if by_res:
        sizes = by_res.get((resolution or "").lower()) or by_res.get("720p") or next(iter(by_res.values()))
        return sizes.get(aspect_ratio) or sizes["16:9"]
    if model and model in _RUNWARE_MODEL_DIMENSION_OVERRIDES:
        overrides = _RUNWARE_MODEL_DIMENSION_OVERRIDES[model]
        return overrides.get(aspect_ratio, next(iter(overrides.values())))
    return _VIDEO_DIMENSIONS.get(aspect_ratio, (1280, 720))


def _resolve_duration(runware_model_air: str, requested: str) -> float:
    constraint = _RUNWARE_VIDEO_DURATION_CONSTRAINTS.get(runware_model_air, {})
    default = constraint.get("default", 5.0)
    allowed = constraint.get("allowed")
    try:
        v = float(requested)
    except (ValueError, TypeError):
        return default
    if not allowed:
        return v
    nearest = min(allowed, key=lambda x: abs(x - v))
    return nearest


async def generate_image_runware(
    api_key: str,
    runware_model_air: str,
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    num_images: int = 1,
    reference_images: Optional[List[str]] = None,
) -> List[str]:
    from runware import Runware, IImageInference

    if not api_key:
        raise ValueError("RUNWARE_KEY is not configured")

    refs = [u for u in (reference_images or []) if u]
    runware = Runware(api_key=api_key)
    async with call("runware", "connect", model=runware_model_air):
        await runware.connect()
    inference_kwargs = dict(
        positivePrompt=prompt,
        model=runware_model_air,
        width=width,
        height=height,
        numberResults=num_images,
        outputFormat="PNG",
    )
    if refs:
        # Pass the source image(s) so reference-capable models can edit/condition.
        inference_kwargs["referenceImages"] = refs
    req = IImageInference(**inference_kwargs)
    async with call("runware", "image_inference", model=runware_model_air, width=width, height=height,
                    requested=num_images, refs=len(refs)) as _log:
        images = await runware.imageInference(requestImage=req)
        _log["results"] = size(images)
    urls = [img.imageURL for img in images if img.imageURL]
    logger.info(f"✅ [Runware] {len(urls)} image(s) via {runware_model_air}")
    return urls


async def generate_video_runware(
    api_key: str,
    runware_model_air: str,
    prompt: str,
    aspect_ratio: str = "16:9",
    duration: str = "5",
    *,
    resolution: Optional[str] = None,
    start_image: Optional[str] = None,
    end_image: Optional[str] = None,
    audio: Optional[bool] = None,
) -> str:
    from runware import Runware, IVideoInference, IFrameImage

    if not api_key:
        raise ValueError("RUNWARE_KEY is not configured")

    # Runware requires an integer number of seconds.
    duration_secs = int(round(_resolve_duration(runware_model_air, duration)))
    width, height = aspect_ratio_to_video_dimensions(aspect_ratio, model=runware_model_air, resolution=resolution)
    # Start/end keyframes go in `frameImages` ({inputImage, frame: first|last}).
    frames = [IFrameImage(inputImage=img, frame=pos) for img, pos in ((start_image, "first"), (end_image, "last")) if img]

    runware = Runware(api_key=api_key)
    async with call("runware", "connect", model=runware_model_air):
        await runware.connect()
    req = IVideoInference(
        positivePrompt=prompt,
        model=runware_model_air,
        width=width,
        height=height,
        duration=duration_secs,
        numberResults=1,
        deliveryMethod="sync",
        frameImages=frames,
        settings={"audio": bool(audio)} if audio is not None and runware_model_air in _RUNWARE_AUDIO_SETTING else None,
    )
    async with call("runware", "video_inference", model=runware_model_air, width=width, height=height,
                    duration_s=duration_secs, frames=len(frames)) as _log:
        videos = await runware.videoInference(requestVideo=req)
        _log["results"] = size(videos)
    if not videos:
        raise RuntimeError(f"Runware returned no video for {runware_model_air}")
    return videos[0].videoURL
