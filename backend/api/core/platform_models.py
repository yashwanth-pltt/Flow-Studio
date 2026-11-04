"""The platform's own models first: `ctx.llm` for text, `ctx.media` for images, video and voice.

Both run on the platform's keys and the organization's monthly budget (manifest `platform_services`), so they lead
every chain; the app's own keys (Anthropic / OpenAI / Gemini, the router, fal, Runware, ElevenLabs) stay behind them
as fallbacks. A person's explicit choice of a model still runs first — the platform leads for the default / "palette".

* Text: the best model this workspace may use is picked from `ctx.llm.catalog()` (`PLATFORM_MODELS` first, within
  its output-token and image limits) and run exactly (`fallback=False`). When the catalog lists none (billing off,
  an org policy, budget spent) the platform steps aside **without a call** and the next provider is used.
* Media: a fal model runs on the platform only when `ctx.media.catalog()` lists that exact id as available. A job
  that outlives the poll is collected from its durable handle (never submitted — and paid for — twice), and the
  keyless `pltt dev` stub is not a result.

Catalogs are reused for `CATALOG_TTL` seconds per organization.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("pltt_video_studio_2.platform_models")

#: The platform's chat models, best first. Premium ones are declared in palette-plugin.json
#: (platform_services.llm.models); the catalog says which this workspace may run.
PLATFORM_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "gpt-5.5", "gemini-3.1-pro-preview", "palette-llm")
#: The platform's limits when a catalog row doesn't state them (the app's manifest can raise them).
DEFAULT_MAX_OUTPUT_TOKENS = 4096
DEFAULT_MAX_IMAGES = 4
#: How long a workspace's model catalog is reused before it is asked again.
CATALOG_TTL = 600.0
#: The platform's ElevenLabs voice (ctx.media.speech).
PLATFORM_TTS = "fal-ai/elevenlabs/tts/turbo-v2.5"
PLATFORM_DEFAULT_VOICE = "Rachel"


# Bound at import: the Palette OS loader swaps `palette_sdk` between plugins, so it
# must not be imported inside functions. Older SDKs lack MediaStillRunning.
try:
    from palette_sdk.platform_services import MediaStillRunning as _MediaStillRunning
except Exception:  # noqa: BLE001 — an SDK without it
    _MediaStillRunning = TimeoutError

class PlatformUnavailable(RuntimeError):
    """The platform can't serve this call (no live service, nothing this workspace may use, no result)."""


# --- services ---------------------------------------------------------------------------------------------------------

def _live(service: Any) -> Any:
    """`service` when it is a live one: not absent, not the undeclared stand-in, not the keyless dev stub."""
    if service is None or type(service).__name__ == "UnavailablePlatformService":
        return None
    try:
        if getattr(service, "live", True) is False:
            return None
    except Exception:  # the undeclared stand-in raises on any attribute
        return None
    return service


def llm_service(ctx: Any) -> Any:
    """`ctx.llm` when this runtime injected a live one."""
    service = _live(getattr(ctx, "llm", None))
    return service if service is not None and callable(getattr(service, "chat", None)) else None


def media_service(ctx: Any) -> Any:
    """`ctx.media` when this runtime injected a live one."""
    service = _live(getattr(ctx, "media", None))
    return service if service is not None and callable(getattr(service, "run", None)) else None


# --- catalogs ---------------------------------------------------------------------------------------------------------

_chat_catalogs: dict[Any, tuple[float, list[dict] | None]] = {}
_media_catalogs: dict[Any, tuple[float, list[dict] | None]] = {}


def clear_caches() -> None:
    _chat_catalogs.clear()
    _media_catalogs.clear()


def _cached(cache: dict, ctx: Any) -> tuple[bool, list[dict] | None]:
    hit = cache.get(getattr(ctx, "organization_id", None))
    if hit and time.monotonic() - hit[0] < CATALOG_TTL:
        return True, hit[1]
    return False, None


async def chat_catalog(ctx: Any) -> list[dict] | None:
    """The workspace's chat models (`ctx.llm.catalog()`); None when it can't be read (the platform then chooses)."""
    service = llm_service(ctx)
    if service is None:
        return []
    found, rows = _cached(_chat_catalogs, ctx)
    if found:
        return rows
    try:
        rows = await service.catalog()
        rows = [r for r in rows or [] if isinstance(r, dict) and r.get("kind", "chat") == "chat"]
    except Exception as exc:  # an older platform without a catalog
        logger.info("ctx.llm.catalog() unavailable: %s", exc)
        rows = None
    _chat_catalogs[getattr(ctx, "organization_id", None)] = (time.monotonic(), rows)
    return rows


async def media_catalog(ctx: Any) -> list[dict]:
    """The workspace's media models (`ctx.media.catalog()`); [] when there is no live service or no catalog."""
    service = media_service(ctx)
    if service is None:
        return []
    found, rows = _cached(_media_catalogs, ctx)
    if found:
        return rows or []
    try:
        rows = [r for r in await service.catalog() or [] if isinstance(r, dict)]
    except Exception as exc:  # no catalog: nothing is known to be available
        logger.info("ctx.media.catalog() unavailable: %s", exc)
        rows = []
    _media_catalogs[getattr(ctx, "organization_id", None)] = (time.monotonic(), rows)
    return rows


async def warm(ctx: Any) -> None:
    """Read both catalogs (if stale) so the synchronous `/models` report sees them."""
    await asyncio.gather(chat_catalog(ctx), media_catalog(ctx), return_exceptions=True)


def cached_chat_rows(ctx: Any) -> tuple[bool, list[dict] | None]:
    """(known, rows) from the cache, without asking the platform."""
    return _cached(_chat_catalogs, ctx)


def media_listed(ctx: Any, model_id: str) -> bool:
    """Synchronous `media_available` from the cache (False until the catalog was read)."""
    if media_service(ctx) is None:
        return False
    _found, rows = _cached(_media_catalogs, ctx)
    return any(r.get("id") == model_id and r.get("available") for r in rows or [])


async def media_available(ctx: Any, model_id: str | None) -> bool:
    """True only when the platform catalog lists exactly `model_id` as available to this workspace."""
    if not model_id or media_service(ctx) is None:
        return False
    return any(r.get("id") == model_id and r.get("available") for r in await media_catalog(ctx))


# --- text ---------------------------------------------------------------------------------------------------------------

def _limits(row: dict) -> dict:
    limits = dict(row.get("limits") or {})
    limits.setdefault("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
    limits.setdefault("max_images", DEFAULT_MAX_IMAGES)
    return limits


def pick(rows: list[dict] | None, images: int = 0) -> tuple[str | None, dict] | None:
    """(model, limits) to run: the first of PLATFORM_MODELS the workspace may use that takes this many images, else
    any available chat model that does. (None, {}) when the catalog is unknown (the platform chooses); None when
    nothing usable is available."""
    if rows is None:
        return None, {}
    usable = [r for r in rows if r.get("available")]

    def fits(r: dict) -> bool:
        if not images:
            return True
        limit = _limits(r)["max_images"]
        return "vision" in (r.get("capabilities") or []) and (limit is None or images <= int(limit))

    by_id = {r.get("id"): r for r in usable}
    for model in PLATFORM_MODELS:
        if model in by_id and fits(by_id[model]):
            return model, _limits(by_id[model])
    for r in usable:
        if fits(r):
            return r.get("id"), _limits(r)
    return None


def unavailable_reasons(rows: list[dict] | None) -> str:
    reasons = sorted({str(r.get("reason")) for r in rows or [] if not r.get("available") and r.get("reason")})
    return ", ".join(reasons) or "no chat model"


def _image_count(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for p in content if isinstance(p, dict) and p.get("type") in ("image_url", "image"))


async def platform_chat(ctx: Any, system: str, content: Any, max_tokens: int, **kw: Any) -> tuple[str, dict]:
    """One `ctx.llm.chat` call on the best model this workspace may use → (text, receipt).

    `content` is a string or OpenAI-style parts (`text` / `image_url`). Raises PlatformUnavailable — without calling
    the platform — when the catalog lists no usable model (the reasons are in the message)."""
    service = llm_service(ctx)
    if service is None:
        raise PlatformUnavailable("platform llm unavailable")
    rows = await chat_catalog(ctx)
    chosen = pick(rows, _image_count(content))
    if chosen is None:
        raise PlatformUnavailable(f"platform: no model this workspace may use ({unavailable_reasons(rows)})")
    model, limits = chosen
    cap = int(limits.get("max_output_tokens") or max_tokens)
    receipt = await service.chat([{"role": "user", "content": content}], model=model, system=system,
                                 max_tokens=max(1, min(int(max_tokens), cap)), fallback=False, **kw)
    if not isinstance(receipt, dict):
        receipt = {"text": str(receipt or "")}
    receipt.setdefault("model", model)
    return receipt.get("text") or "", receipt


# --- media --------------------------------------------------------------------------------------------------------------

def _still_running_type() -> type:
    return _MediaStillRunning


def _checked(result: Any) -> dict:
    if not isinstance(result, dict) or result.get("pltt_dev_stub"):
        raise PlatformUnavailable("the platform media service is not live here (no result)")
    return result


async def _collect(media: Any, model: str, still: BaseException, deadline: float) -> dict:
    """Poll a job that outlived `run` from its durable handle until it finishes (or `deadline`)."""
    still_running = _still_running_type()
    loop = asyncio.get_running_loop()
    handle = getattr(still, "handle", None) or {"request_id": getattr(still, "request_id", ""), "model": model}
    while True:
        await asyncio.sleep(5)
        try:
            return await media.result(handle, model=model)
        except still_running:
            if loop.time() > deadline:
                raise PlatformUnavailable(f"{model} is still running on the platform") from None


async def platform_run(ctx: Any, model: str, payload: dict, timeout: float = 600.0) -> dict:
    """`ctx.media.run(model, payload)`. A job still running after `timeout` is collected from its handle (for up to
    five more minutes) instead of being run — and paid for — again elsewhere."""
    media = media_service(ctx)
    if media is None:
        raise PlatformUnavailable("platform media unavailable")
    still_running = _still_running_type()
    deadline = asyncio.get_running_loop().time() + timeout + 300
    try:
        result = await media.run(model, payload, timeout=timeout)
    except still_running as still:
        result = await _collect(media, model, still, deadline)
    return _checked(result)


async def platform_speech(ctx: Any, text: str, *, voice: str | None = None, model: str = PLATFORM_TTS,
                          timeout: float = 180.0, **payload: Any) -> dict:
    """`ctx.media.speech` with an ElevenLabs voice (a premade name or a voice id) → the media result
    (its audio at `audio.url`)."""
    media = media_service(ctx)
    if media is None:
        raise PlatformUnavailable("platform media unavailable")
    still_running = _still_running_type()
    deadline = asyncio.get_running_loop().time() + timeout + 300
    try:
        result = await media.speech(text, model=model, voice=voice or PLATFORM_DEFAULT_VOICE, **payload)
    except still_running as still:
        result = await _collect(media, model, still, deadline)
    return _checked(result)


def result_url(result: dict, *keys: str) -> str | None:
    """The first file url under `keys` (a {"url"} object, a bare url, or a list of them)."""
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get("url")
        if isinstance(value, str) and value:
            return value
    return None
