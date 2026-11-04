"""pltt LLM Router gateway helper.

The router (https://router-api.pltt.ai) is a provider-abstracted gateway. We use
it as the PRIMARY provider for image generation / edit / upscale, falling back
to fal and then runware when it's disabled, has no equivalent model, or errors.

It streams normalized "Gateway Events" over Server-Sent Events; the produced
image URL(s) arrive in `image_done` events at `data.url`. See
https://router-api.pltt.ai/docs and /v1/models for the catalog.

Config (env):
  LLM_ROUTER_ENABLED   "true" to use the router first
  LLM_ROUTER_API_KEY   gateway developer key (Authorization: Bearer …)
  LLM_ROUTER_BASE_URL  default https://router-api.pltt.ai
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from importlib import import_module
from typing import Optional

from backend.api.core.runware_helper import aspect_ratio_to_dimensions
from backend.api.core.secrets import read_secret
from backend.api.core.std_logs import call

logger = logging.getLogger("router_helper")


def _load_env() -> None:
    try:
        import_module("dotenv").load_dotenv()
    except Exception:
        pass


_load_env()

def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


# Import-time defaults from the process env — these cover `pltt dev` (which loads
# `.env`). In the PUBLISHED runtime the router config lives in the platform
# secret store, NOT the process env, so `apply_secrets(ctx)` (called per request
# from the generation routes) re-resolves them via the SDK secret reader. Without
# that, `router_enabled()` is False in production and every model falls to fal.
ROUTER_ENABLED = _truthy(os.getenv("LLM_ROUTER_ENABLED", ""))
ROUTER_API_KEY = os.getenv("LLM_ROUTER_API_KEY")
ROUTER_BASE_URL = (os.getenv("LLM_ROUTER_BASE_URL") or "https://router-api.pltt.ai").rstrip("/")


def apply_secrets(ctx) -> None:
    """Resolve the router config from the platform secret store (config secrets
    → env → SDK resolver) so the router works in the published runtime, not only
    under `pltt dev`. Safe to call on every request; only non-empty values
    override the import-time defaults."""
    global ROUTER_ENABLED, ROUTER_API_KEY, ROUTER_BASE_URL
    enabled = read_secret(ctx, "LLM_ROUTER_ENABLED")
    if enabled:
        ROUTER_ENABLED = _truthy(enabled)
    key = read_secret(ctx, "LLM_ROUTER_API_KEY")
    if key:
        ROUTER_API_KEY = key
    base = read_secret(ctx, "LLM_ROUTER_BASE_URL")
    if base:
        ROUTER_BASE_URL = base.rstrip("/")

# App model name -> router model alias (catalog ids from /v1/models), chosen to
# request the SAME underlying model the app uses today.
#   nano_banana_pro -> image-production       (fal-nano-banana-pro-image)
#   flux_schnell    -> schnell
# GPT Image 2.5 has no verified router alias yet, so it is not mapped: the router
# steps aside and the platform / fal serve it. (An unknown alias would silently
# fall back to nano-banana, which is why nothing is guessed here.)
ROUTER_TEXT_TO_IMAGE_MODELS = {
    "nano_banana_pro": "image-production",
    "flux_schnell": "schnell",
}
#   nano_banana_pro -> image-edit-production    (fal-nano-banana-pro-edit)
ROUTER_EDIT_MODELS = {
    "nano_banana_pro": "image-edit-production",
}
# SeedVR upscale, same model the app uses today (fal-seedvr-image-upscale).
ROUTER_UPSCALE_SEEDVR = "palette-upscale-seedvr"
# Midjourney via TTAPI, behind the router.
ROUTER_MIDJOURNEY_MODEL = "midjourney-image-default"


class RouterUnavailable(Exception):
    """Router is disabled/unconfigured, or has no equivalent for the model —
    the caller should fall through to fal/runware without treating it as a
    hard error."""


def router_enabled() -> bool:
    """True when the router should be tried first."""
    return ROUTER_ENABLED and bool(ROUTER_API_KEY)


def router_text_to_image_model(model_name: str) -> Optional[str]:
    return ROUTER_TEXT_TO_IMAGE_MODELS.get(model_name)


def router_edit_model(model_name: str) -> Optional[str]:
    return ROUTER_EDIT_MODELS.get(model_name)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {ROUTER_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


async def _stream_image_urls(path: str, payload: dict, timeout: float = 300.0) -> list[str]:
    """POST to a router route and collect image URLs from the SSE stream.
    Raises on an `error` event or transport failure; returns [] if the stream
    ends without producing an image."""
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx is not installed.")

    urls: list[str] = []
    async with httpx.AsyncClient(timeout=timeout) as client, call(
        "router", path, model=str(payload.get("model") or "") if isinstance(payload, dict) else ""
    ) as _log:
        async with client.stream(
            "POST", f"{ROUTER_BASE_URL}{path}", json=payload, headers=_headers()
        ) as resp:
            _log["http_status"] = resp.status_code
            if resp.status_code >= 400:
                body = (await resp.aread()).decode(errors="replace")
                raise RuntimeError(f"Router HTTP {resp.status_code}: {body[:300]}")
            async for raw in resp.aiter_lines():
                line = raw.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue
                d = evt.get("data") or {}
                etype = evt.get("event") or d.get("type")
                if etype in ("image_done", "video_done") and d.get("url"):
                    urls.append(d["url"])
                elif etype == "error":
                    _log["images"] = len(urls)
                    raise RuntimeError(f"Router error event: {d or evt}")
            _log["images"] = len(urls)
    return urls


# Aspect/size MUST be set per provider under `provider_options`, NOT via a
# top-level `size`. Verified against the live router (2026): the gemini alias
# (`image-staging`) and — critically — `image-production` (fal nano-banana-pro,
# our main model) IGNORE a top-level `size` and fall back to 1024x1024 square;
# `image-production` only honors `provider_options.fal.aspect_ratio`. Because the
# gateway runs its OWN provider fallbacks, we set every provider's field (it
# forwards only the section matching the chosen provider), so the requested
# aspect is honored whichever provider ends up serving the model.
_FAL_IMAGE_SIZE = {
    "1:1": "square_hd",
    "16:9": "landscape_16_9",
    "9:16": "portrait_16_9",
    "4:3": "landscape_4_3",
    "3:4": "portrait_4_3",
    "21:9": "landscape_16_9",
    "2:3": "portrait_4_3",
    "3:2": "landscape_4_3",
}
# OpenAI gpt-image only accepts square / portrait / landscape buckets.
_OPENAI_SIZE = {
    "landscape": "1536x1024",
    "portrait": "1024x1536",
    "square": "1024x1024",
}


def _orientation(aspect_ratio: str) -> str:
    try:
        w, h = (float(x) for x in aspect_ratio.split(":"))
        if w > h:
            return "landscape"
        if h > w:
            return "portrait"
    except Exception:
        pass
    return "square"


def _image_size_options(
    aspect_ratio: Optional[str], resolution: Optional[str], num_images: int
) -> dict:
    """Build `provider_options` carrying the requested aspect/size in each
    provider's own field names (fal `aspect_ratio`/`image_size`, openai `size`,
    runware `width`/`height`, gemini `aspect_ratio`)."""
    ar = (aspect_ratio or "").strip() or "1:1"
    n = max(1, int(num_images or 1))
    fal: dict = {"aspect_ratio": ar, "image_size": _FAL_IMAGE_SIZE.get(ar, "square_hd")}
    gemini: dict = {"aspect_ratio": ar}
    openai: dict = {"size": _OPENAI_SIZE[_orientation(ar)]}
    if n > 1:
        fal["num_images"] = n
        gemini["sample_count"] = n
    opts: dict = {"fal": fal, "gemini": gemini, "openai": openai}
    try:
        w, h = aspect_ratio_to_dimensions(ar, (resolution or "1K"))
        runware: dict = {"width": int(w), "height": int(h)}
        if n > 1:
            runware["numberResults"] = n
        opts["runware"] = runware
    except Exception:
        pass
    return opts


async def generate_image_router(
    model_name: str,
    prompt: str,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    num_images: int = 1,
    negative_prompt: Optional[str] = None,
) -> list[str]:
    """Text-to-image via the router. Returns image URLs. Raises
    RouterUnavailable when the router can't serve this model."""
    if not router_enabled():
        raise RouterUnavailable("router disabled or unconfigured")
    model = router_text_to_image_model(model_name)
    if not model:
        raise RouterUnavailable(f"no router text-to-image model for '{model_name}'")
    options = _image_size_options(aspect_ratio, resolution, num_images)
    if negative_prompt:
        options["fal"]["negative_prompt"] = negative_prompt
        options.setdefault("runware", {})["negativePrompt"] = negative_prompt
    payload: dict = {"prompt": prompt, "model": model, "provider_options": options}
    n = max(1, int(num_images or 1))
    logger.info(
        f"🛰️  [Router] /image model={model} aspect_ratio={aspect_ratio} "
        f"resolution={resolution} n={n}"
    )
    urls = await _stream_image_urls("/image", payload)
    # Some providers return one image per call and ignore the batch count
    # (verified: OpenAI gpt-image-2 yields 1 even with provider_options.openai.n),
    # so top up with extra concurrent calls until we have the requested count.
    if len(urls) < n:
        extra = await asyncio.gather(
            *[_stream_image_urls("/image", dict(payload)) for _ in range(n - len(urls))]
        )
        for batch in extra:
            urls.extend(batch)
    return urls[:n]


async def edit_image_router(
    model_name: str,
    prompt: str,
    image_url: str,
    reference_images: Optional[list[str]] = None,
) -> list[str]:
    """Image edit via the router. The image being edited is the `subject`; any
    extra references are `style_reference`. Raises RouterUnavailable when the
    router can't serve this model."""
    if not router_enabled():
        raise RouterUnavailable("router disabled or unconfigured")
    model = router_edit_model(model_name)
    if not model:
        raise RouterUnavailable(f"no router edit model for '{model_name}'")
    inputs = [{"type": "image", "url": image_url, "role": "subject"}]
    for ref in (reference_images or []):
        if ref:
            inputs.append({"type": "image", "url": ref, "role": "style_reference"})
    payload = {"prompt": prompt, "model": model, "inputs": inputs}
    logger.info(f"🛰️  [Router] /image/edit model={model} refs={len(inputs) - 1}")
    return await _stream_image_urls("/image/edit", payload)


async def upscale_image_router(
    image_url: str, model: str = ROUTER_UPSCALE_SEEDVR
) -> list[str]:
    """Image upscale via the router (SeedVR by default). Raises
    RouterUnavailable when the router is disabled."""
    if not router_enabled():
        raise RouterUnavailable("router disabled or unconfigured")
    payload = {"model": model, "inputs": [{"type": "image", "url": image_url}]}
    logger.info(f"🛰️  [Router] /image/upscale model={model}")
    return await _stream_image_urls("/image/upscale", payload)


# --------------------------------------------------------------------------- #
# Midjourney (TTAPI behind the router)
# --------------------------------------------------------------------------- #

# App action labels the router /midjourney/action can serve. The router supports
# only upscale / variation / reroll (NOT zoom/pan/make-square/etc.).
def router_midjourney_action(label: str) -> Optional[dict]:
    """Map an app action label (U1-U4, V1-V4, reroll) to the router action
    payload {action, position?}. Returns None for actions the router can't do
    (zoom/pan/make-square/…), so the caller uses direct TTAPI instead."""
    a = (label or "").strip()
    up = a.upper()
    if len(up) == 2 and up[0] in ("U", "V") and up[1:].isdigit():
        return {"action": "upscale" if up[0] == "U" else "variation", "position": int(up[1:])}
    if a.lower() == "reroll":
        return {"action": "reroll"}
    return None


async def _stream_midjourney(path: str, payload: dict, timeout: float = 600.0) -> dict:
    """POST to a router Midjourney route and collect the result from the SSE
    stream. Returns {"image_url", "job_id", "items"} — Midjourney results arrive
    in `items_done` (items carry url + provider_job_id + position)."""
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx is not installed.")

    items: list = []
    image_url: Optional[str] = None
    job_id: Optional[str] = None
    async with httpx.AsyncClient(timeout=timeout) as client, call("router", path) as _log:
        async with client.stream(
            "POST", f"{ROUTER_BASE_URL}{path}", json=payload, headers=_headers()
        ) as resp:
            _log["http_status"] = resp.status_code
            if resp.status_code >= 400:
                body = (await resp.aread()).decode(errors="replace")
                raise RuntimeError(f"Router HTTP {resp.status_code}: {body[:300]}")
            async for raw in resp.aiter_lines():
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue
                d = evt.get("data") or {}
                etype = evt.get("event") or d.get("type")
                if etype == "items_done":
                    for it in (d.get("items") or []):
                        items.append(it)
                        if it.get("url") and not image_url:
                            image_url = it.get("url")
                        if it.get("provider_job_id") and not job_id:
                            job_id = it.get("provider_job_id")
                elif etype == "image_done" and d.get("url"):
                    image_url = d["url"]
                    job_id = d.get("provider_job_id") or job_id
                elif etype == "error":
                    raise RuntimeError(f"Router error event: {d or evt}")
            _log["items"] = len(items)
            _log["image"] = bool(image_url)
            _log["job_id"] = job_id
    return {"image_url": image_url, "job_id": job_id, "items": items}


async def midjourney_imagine_router(prompt: str, mode: str = "fast") -> dict:
    """Start a Midjourney imagine grid via the router. Returns
    {image_url (grid), job_id (provider job), items}."""
    if not router_enabled():
        raise RouterUnavailable("router disabled or unconfigured")
    payload = {"prompt": prompt, "model": ROUTER_MIDJOURNEY_MODEL, "mode": mode}
    logger.info("🛰️  [Router] /midjourney/imagine")
    return await _stream_midjourney("/midjourney/imagine", payload)


async def midjourney_action_router(
    provider_job_id: str, action: str = "upscale", position: int = 1
) -> dict:
    """Run a Midjourney action (upscale/variation/reroll) on a router job.
    Returns {image_url, job_id (new provider job), items}."""
    if not router_enabled():
        raise RouterUnavailable("router disabled or unconfigured")
    payload: dict = {
        "provider_job_id": provider_job_id,
        "action": action,
        "model": ROUTER_MIDJOURNEY_MODEL,
    }
    if action in ("upscale", "upsample", "variation"):
        payload["position"] = position
    logger.info(f"🛰️  [Router] /midjourney/action action={action} pos={position}")
    return await _stream_midjourney("/midjourney/action", payload)


async def midjourney_generate_router(prompt: str, mode: str = "fast") -> dict:
    """Full Midjourney create via the router: imagine a grid, then upscale
    position 1 — mirroring the app's TTAPI create. Returns
    {grid_job_id, grid_url, image_url, upscale_job_id}."""
    grid = await midjourney_imagine_router(prompt, mode)
    grid_job = grid.get("job_id")
    if not grid_job:
        raise RuntimeError("Router midjourney imagine returned no job id")
    up = await midjourney_action_router(grid_job, "upscale", 1)
    final = up.get("image_url")
    if not final:
        raise RuntimeError("Router midjourney U1 upscale returned no image")
    return {
        "grid_job_id": grid_job,
        "grid_url": grid.get("image_url"),
        "image_url": final,
        "upscale_job_id": up.get("job_id") or grid_job,
    }
