"""Text LLM access for planning, motion prompts, chat and reference analysis.

The user picks a text model on the home page (`GET /models` → `text`). Every
call returns parsed JSON and falls back through the other configured
providers when one fails (`provider_order`):

  * "palette" — the platform LLM service `ctx.llm` (manifest
    `platform_services.llm`), billed to the org under its spend cap. It goes
    FIRST for the default / "palette" / auto: the best model the workspace's
    catalog allows (`platform_models.pick`), run exactly, within its output
    and image limits; with no usable model it steps aside without a call.
  * direct provider keys — Anthropic / OpenAI / Gemini (plugin secrets). A
    provider the person explicitly chose runs first, then the platform, then
    the rest.
  * "router" — the pltt LLM Router's OpenAI-compatible chat endpoint on
    LLM_ROUTER_API_KEY (the same gateway the image routes use).

Ported from the storyboard app's story_video.py, plus the platform provider.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import httpx
from fastapi import HTTPException

from backend.api.core import platform_models
from backend.api.core.secrets import read_secret
from backend.api.core.std_logs import call as log_call, llm_usage, size

logger = logging.getLogger("pltt_video_studio_2.llm")


def parse_json_block(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in LLM reply: {t[:200]!r}")
    return json.loads(t[start : end + 1])


# --- Direct providers ---------------------------------------------------------

async def _openai_json(api_key: str, system: str, user: str, model: str) -> dict:
    model = os.getenv("AGENT_OPENAI_MODEL", model)
    async with httpx.AsyncClient(timeout=180) as client, log_call("openai", "chat_json", model=model) as _log:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
        _log["http_status"] = r.status_code
        r.raise_for_status()
        _log.update(llm_usage(r))
        return parse_json_block(r.json()["choices"][0]["message"]["content"])


async def _gemini_json(api_key: str, system: str, user: str, model: str) -> dict:
    model = os.getenv("AGENT_GEMINI_MODEL", model)
    async with httpx.AsyncClient(timeout=180) as client, log_call("gemini", "chat_json", model=model) as _log:
        r = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
        )
        _log["http_status"] = r.status_code
        r.raise_for_status()
        _log.update(llm_usage(r))
        return parse_json_block(r.json()["candidates"][0]["content"]["parts"][0]["text"])


async def _anthropic_json(api_key: str, system: str, user: str, model: str, max_tokens: int = 8192) -> dict:
    model = os.getenv("AGENT_ANTHROPIC_MODEL", model)
    async with httpx.AsyncClient(timeout=180) as client, log_call("anthropic", "chat_json", model=model) as _log:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]},
        )
        _log["http_status"] = r.status_code
        r.raise_for_status()
        _log.update(llm_usage(r))
        data = r.json()
        return parse_json_block("".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"))


async def _openai_vision_json(api_key: str, system: str, text: str, image_b64: str, mime: str, model: str) -> dict:
    model = os.getenv("AGENT_OPENAI_MODEL", model)
    async with httpx.AsyncClient(timeout=180) as client, log_call("openai", "vision_json", model=model, images=1) as _log:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    ]},
                ],
            },
        )
        _log["http_status"] = r.status_code
        r.raise_for_status()
        _log.update(llm_usage(r))
        return parse_json_block(r.json()["choices"][0]["message"]["content"])


async def _gemini_vision_json(api_key: str, system: str, text: str, image_b64: str, mime: str, model: str) -> dict:
    model = os.getenv("AGENT_GEMINI_MODEL", model)
    async with httpx.AsyncClient(timeout=180) as client, log_call("gemini", "vision_json", model=model, images=1) as _log:
        r = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"inline_data": {"mime_type": mime, "data": image_b64}}, {"text": text}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
        )
        _log["http_status"] = r.status_code
        r.raise_for_status()
        _log.update(llm_usage(r))
        return parse_json_block(r.json()["candidates"][0]["content"]["parts"][0]["text"])


async def _anthropic_vision_json(api_key: str, system: str, text: str, image_b64: str, mime: str, model: str,
                                 max_tokens: int = 8192) -> dict:
    model = os.getenv("AGENT_ANTHROPIC_MODEL", model)
    async with httpx.AsyncClient(timeout=180) as client, log_call("anthropic", "vision_json", model=model, images=1) as _log:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
                    {"type": "text", "text": text},
                ]}],
            },
        )
        _log["http_status"] = r.status_code
        r.raise_for_status()
        _log.update(llm_usage(r))
        data = r.json()
        return parse_json_block("".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"))


ROUTER_DEFAULT_BASE_URL = "https://router-api.pltt.ai"


def sse_text(lines: Any) -> str:
    """The reply text of an OpenAI-style chat stream (`data: {chunk}` lines)."""
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if chunk.get("error"):
            raise RuntimeError(f"router error: {str(chunk['error'])[:300]}")
        for choice in chunk.get("choices") or []:
            out.append((choice.get("delta") or {}).get("content") or (choice.get("message") or {}).get("content") or "")
    return "".join(out)


async def _router_chat(api_key: str, messages: list, model: str, max_tokens: int, base_url: Optional[str],
                       name: str, images: Optional[int] = None) -> dict:
    # The router always streams, even with "stream": false.
    url = f"{(base_url or os.getenv('LLM_ROUTER_BASE_URL') or ROUTER_DEFAULT_BASE_URL).rstrip('/')}/v1/chat/completions"
    async with httpx.AsyncClient(timeout=180) as client, log_call("router", name, model=model, images=images) as _log:
        async with client.stream(
            "POST", url, headers={"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"},
            json={"model": model, "max_tokens": max_tokens, "messages": messages},
        ) as r:
            _log["http_status"] = r.status_code
            if r.status_code >= 400:
                raise RuntimeError(f"router HTTP {r.status_code}: {(await r.aread()).decode(errors='replace')[:300]}")
            text = sse_text([line async for line in r.aiter_lines()])
    return parse_json_block(text)


async def _router_json(api_key: str, system: str, user: str, model: str, max_tokens: int = 8192,
                       base_url: Optional[str] = None) -> dict:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return await _router_chat(api_key, messages, model, max_tokens, base_url, "chat_json")


async def _router_vision_json(api_key: str, system: str, text: str, image_b64: str, mime: str, model: str,
                              max_tokens: int = 8192, base_url: Optional[str] = None) -> dict:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ]},
    ]
    return await _router_chat(api_key, messages, model, max_tokens, base_url, "vision_json", images=1)


# id -> (label, provider, secret, model id, text call, vision call)
PROVIDERS: dict[str, tuple[str, str, str, str, Any, Any]] = {
    "claude_opus": ("Claude Opus 5.5", "anthropic", "ANTHROPIC_API_KEY", "claude-opus-5-5", _anthropic_json, _anthropic_vision_json),
    "claude_sonnet": ("Claude Sonnet 5", "anthropic", "ANTHROPIC_API_KEY", "claude-sonnet-5", _anthropic_json, _anthropic_vision_json),
    "claude_haiku": ("Claude Haiku 4.5", "anthropic", "ANTHROPIC_API_KEY", "claude-haiku-4-5-20251001", _anthropic_json, _anthropic_vision_json),
    "gpt": ("GPT-5.5", "openai", "OPENAI_KEY", "gpt-5.5", _openai_json, _openai_vision_json),
    "gemini": ("Gemini 3.1 Pro", "google", "GOOGLE_AI_API_KEY", "gemini-3.1-pro", _gemini_json, _gemini_vision_json),
    # `chat-claude` is the router alias for its Claude model (vision included).
    "router": ("LLM Router (Claude)", "router", "LLM_ROUTER_API_KEY", "chat-claude", _router_json, _router_vision_json),
}
PALETTE = "palette"
DEFAULT_MAX_TOKENS = 8192


# --- Platform provider (ctx.llm) ----------------------------------------------

def platform_llm(ctx: Any) -> Any | None:
    """`ctx.llm` when this runtime injected a live one.

    Runtimes without the service inject `UnavailablePlatformService`, whose
    attribute access raises; `pltt dev` without a key injects a stub whose
    `live` is False and whose replies are canned text — useless for JSON.
    """
    return platform_models.llm_service(ctx)


async def _palette_json(
    ctx: Any, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS,
    image_b64: Optional[str] = None, mime: Optional[str] = None,
) -> dict:
    """The best model this workspace may use (catalog pick, run exactly with
    `fallback=False`, `max_tokens` capped by its limits). Pictures go only to
    a vision model that takes them; with no usable model this raises without
    calling the platform."""
    content: Any = user
    if image_b64:
        content = [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": f"data:{mime or 'image/png'};base64,{image_b64}"}},
        ]
    async with log_call("ctx.llm", "vision_json" if image_b64 else "chat_json", max_tokens=max_tokens,
                        images=1 if image_b64 else None) as _log:
        text, receipt = await platform_models.platform_chat(
            ctx, system + "\nReply with a single JSON object only.", content, max_tokens, temperature=0.4,
        )
        _log["model"] = receipt.get("model") or ""
        for src, dst in (("input_tokens", "tokens_in"), ("output_tokens", "tokens_out")):
            if isinstance(receipt.get(src), int):
                _log[dst] = receipt[src]
        if receipt.get("truncated"):
            _log["message"] = "the answer was cut off at max_tokens"
    return parse_json_block(text or "")


# --- Catalog & dispatch ---------------------------------------------------------

def _palette_status(ctx: Any) -> tuple[bool, Optional[str]]:
    """(service live, why no model is usable right now — from the cached catalog; None when one is or unknown)."""
    if platform_llm(ctx) is None:
        return False, None
    known, rows = platform_models.cached_chat_rows(ctx)
    if known and platform_models.pick(rows, 0) is None:
        return True, platform_models.unavailable_reasons(rows)
    return True, None


def text_models(ctx: Any) -> list[dict]:
    live, reason = _palette_status(ctx)
    palette = {
        "id": PALETTE, "label": "Palette", "provider": "palette",
        "available": live, "requires": "platform LLM service",
    }
    if reason:
        # The service is connected but no model may run now: calls step aside to the keys below.
        palette["models_ready"] = False
        palette["reason"] = reason
    out = [palette]
    for mid, (label, provider, secret, _model, _c, _v) in PROVIDERS.items():
        out.append({
            "id": mid, "label": label, "provider": provider,
            "available": bool(read_secret(ctx, secret)), "requires": secret,
        })
    return out


def default_text_model(ctx: Any) -> str:
    for m in text_models(ctx):
        if m["available"]:
            return m["id"]
    return "claude_sonnet"


def provider_order(choice: Optional[str]) -> list[str]:
    """Providers in the order they are tried. The platform leads for the
    default / "palette" / auto; a specific model the person chose runs first,
    then the platform, then the rest."""
    choice = (choice or "").strip().lower()
    if choice in PROVIDERS:
        return [choice, PALETTE, *[p for p in PROVIDERS if p != choice]]
    return [PALETTE, *PROVIDERS]


async def llm_json(
    ctx: Any,
    system: str,
    user: str,
    llm: Optional[str] = None,
    image_b64: Optional[str] = None,
    mime: Optional[str] = None,
    language: Optional[str] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict:
    """JSON from the first provider that answers (see `provider_order`)."""
    if language:
        name = {"ko": "Korean", "ja": "Japanese"}.get(language, "English")
        system = f"{system}\nWrite every human-readable string in {name}."

    errors: list[str] = []
    for provider in provider_order(llm):
        if provider == PALETTE:
            if platform_llm(ctx) is None:
                continue
            try:
                return await _palette_json(ctx, system, user, max_tokens, image_b64, mime)
            except Exception as e:  # noqa: BLE001
                errors.append(f"palette: {e}")
                logger.warning("LLM via palette failed: %s", e)
            continue
        _label, _prov, secret, model, call, vision_call = PROVIDERS[provider]
        api_key = read_secret(ctx, secret)
        if not api_key:
            continue
        try:
            extra = {"max_tokens": max_tokens} if _prov in ("anthropic", "router") else {}
            if _prov == "router":
                extra["base_url"] = read_secret(ctx, "LLM_ROUTER_BASE_URL")
            if image_b64:
                return await vision_call(api_key, system, user, image_b64, mime or "image/png", model, **extra)
            return await call(api_key, system, user, model, **extra)
        except Exception as e:  # noqa: BLE001 — try the next provider
            errors.append(f"{provider}: {e}")
            logger.warning("LLM via %s failed: %s", provider, e)
    # Provider names, URLs and key names stay in the server log; users get a plain message.
    if not errors:
        logger.warning("LLM unavailable: no text model configured (ANTHROPIC_API_KEY, OPENAI_KEY, GOOGLE_AI_API_KEY or the Palette LLM service)")
    raise HTTPException(
        status_code=502,
        detail="The text model couldn't answer. Please try again." if errors else "Scene planning isn't available right now.",
    )
