"""Midjourney (TTAPI) helpers, ported from pltt-agg.

API keys are passed in by the caller (read from `ctx.secret("MIDJOURNEY_KEY")`).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import List, Optional

import httpx

from backend.api.core.router_helper import (
    midjourney_action_router as _mj_action_router,
    router_enabled as _router_enabled,
    router_midjourney_action as _router_mj_action,
)
from backend.api.core.std_logs import call

logger = logging.getLogger("pltt_video_studio_2.midjourney")

TTAPI_BASE_URL = "https://api.ttapi.io/midjourney/v1"
_POLL_INTERVAL = 10
_POLL_TIMEOUT = 600

ACTION_MAP = {
    "U1": "upsample1", "U2": "upsample2", "U3": "upsample3", "U4": "upsample4",
    "V1": "variation1", "V2": "variation2", "V3": "variation3", "V4": "variation4",
}

MIDJOURNEY_ALLOWED_RATIOS = {
    "1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "9:21",
    "3:2", "2:3", "5:4", "4:5",
}

_R_TOKEN_PATTERN = re.compile(r"\bR(\d{1,2})(?:\.png)?\b", re.IGNORECASE)
_ORDINAL_WORDS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]

# TTAPI dedupes identical actions against the same parent job and answers with
# e.g. `Job [0a33a924-…] already exists`. The bracketed id is the previously
# created job, so we can recover it and poll that instead of failing.
_EXISTING_JOB_PATTERN = re.compile(
    r"Job\s*\[?\s*([0-9a-fA-F-]{8,})\s*\]?\s*already exists", re.IGNORECASE
)


def _extract_existing_job_id(body, text: Optional[str]) -> Optional[str]:
    message = ""
    if isinstance(body, dict):
        message = str(body.get("message") or "")
    if not message and text:
        message = text
    match = _EXISTING_JOB_PATTERN.search(message or "")
    return match.group(1) if match else None


def _replace_r_tokens(text: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        idx = int(m.group(1))
        if 1 <= idx <= len(_ORDINAL_WORDS):
            return f"{_ORDINAL_WORDS[idx - 1]} image"
        return m.group(0)
    return _R_TOKEN_PATTERN.sub(repl, text)


def _build_prompt(prompt: str, aspect_ratio: Optional[str], image_references: Optional[List[str]] = None) -> str:
    prompt = _replace_r_tokens((prompt or "").strip())
    parts: list[str] = []
    if image_references:
        valid = [u for u in image_references if isinstance(u, str) and u.startswith(("http://", "https://"))]
        if valid:
            parts.append(" ".join(valid))
    if prompt:
        parts.append(prompt)
    final_prompt = " ".join(parts).strip()
    if not final_prompt:
        return final_prompt
    # Normalize the ratio (trim stray whitespace) so a value like "16:9 " isn't
    # silently dropped — Midjourney reads the aspect from the `--ar` flag.
    ar = (aspect_ratio or "").strip()
    if "--ar" not in final_prompt and ar and ar in MIDJOURNEY_ALLOWED_RATIOS:
        final_prompt = f"{final_prompt} --ar {ar}"
        logger.info(f"🎨 [MJ] applying aspect ratio --ar {ar}")
    return final_prompt


def _headers(api_key: str) -> dict:
    if not api_key:
        raise ValueError("MIDJOURNEY_KEY is not configured")
    return {"TT-API-KEY": api_key, "Content-Type": "application/json"}


def _candidate_keys(api_key: Optional[str], fallback_api_key: Optional[str] = None) -> List[str]:
    """Ordered, de-duplicated list of keys to try: primary first, fallback last.

    The fallback (MIDJOURNEY_TT_API_KEY_FALLBACK) is only ever reached when the
    primary MIDJOURNEY_KEY call raises — see `_with_key_fallback`.
    """
    keys: list[str] = []
    for k in (api_key, fallback_api_key):
        k = (k or "").strip()
        if k and k not in keys:
            keys.append(k)
    if not keys:
        raise ValueError("MIDJOURNEY_KEY is not configured")
    return keys


async def _with_key_fallback(api_key, fallback_api_key, op_name, fn):
    """Run `fn(headers)` with the primary key; on failure retry with the fallback.

    The fallback key is tried ONLY if the primary key's attempt raised, so a
    healthy MIDJOURNEY_KEY is never bypassed.
    """
    keys = _candidate_keys(api_key, fallback_api_key)
    last_err: Optional[Exception] = None
    for idx, key in enumerate(keys):
        try:
            async with call("midjourney", op_name, key_attempt=idx + 1):
                return await fn(_headers(key))
        except Exception as e:  # noqa: BLE001 - retry on any primary-key failure
            last_err = e
            if idx + 1 < len(keys):
                logger.warning(
                    f"⚠️ [MJ] key #{idx + 1} failed on {op_name} ({e}); retrying with fallback key"
                )
                continue
            raise
    raise last_err  # pragma: no cover - loop always returns or raises above


async def _poll_until_complete(client: httpx.AsyncClient, headers: dict, job_id: str) -> str:
    deadline = asyncio.get_event_loop().time() + _POLL_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        resp = await client.get(f"{TTAPI_BASE_URL}/fetch", params={"jobId": job_id}, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status") or data.get("data", {}).get("status", "")
        progress = str(data.get("data", {}).get("progress", ""))
        logger.info(f"🎨 [MJ] jobId={job_id} status={status} progress={progress}%")
        if status == "SUCCESS" and progress == "100":
            image_url = data.get("data", {}).get("cdnImage") or data.get("data", {}).get("discordImage")
            if not image_url:
                raise RuntimeError(f"MJ SUCCESS but no image url: {data}")
            return image_url
        if status == "FAILED":
            raise RuntimeError(f"MJ job failed: {data}")
    raise TimeoutError("Midjourney job did not complete in 10 min")


async def submit_midjourney_job(
    api_key: str,
    prompt: str,
    mode: str = "fast",
    aspect_ratio: Optional[str] = None,
    image_references: Optional[List[str]] = None,
    fallback_api_key: Optional[str] = None,
) -> str:
    final_prompt = _build_prompt(prompt, aspect_ratio, image_references)

    async def _do(headers: dict) -> str:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(
                f"{TTAPI_BASE_URL}/imagine",
                json={"prompt": final_prompt, "mode": mode, "version": "7"},
                headers=headers,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != "SUCCESS":
                raise RuntimeError(f"MJ imagine failed: {body}")
            return body["data"]["jobId"]

    return await _with_key_fallback(api_key, fallback_api_key, "imagine", _do)


async def fetch_midjourney_job(api_key: str, job_id: str, fallback_api_key: Optional[str] = None) -> str:
    async def _do(headers: dict) -> str:
        async with httpx.AsyncClient(timeout=30) as c:
            return await _poll_until_complete(c, headers, job_id)

    return await _with_key_fallback(api_key, fallback_api_key, "fetch", _do)


_BLEND_DIMENSIONS = {"PORTRAIT", "SQUARE", "LANDSCAPE"}


async def _to_data_uri(client: httpx.AsyncClient, src: str) -> str:
    """Return `src` as a base64 data: URI. Passes through existing data: URIs;
    otherwise downloads the URL and encodes it (the TTAPI blend endpoint takes
    base64, not URLs)."""
    if src.startswith("data:"):
        return src
    resp = await client.get(src, follow_redirects=True)
    resp.raise_for_status()
    ctype = resp.headers.get("content-type") or "image/png"
    b64 = base64.b64encode(resp.content).decode("ascii")
    return f"data:{ctype};base64,{b64}"


async def blend_midjourney_images(
    api_key: str,
    images: List[str],
    mode: str = "fast",
    dimensions: str = "SQUARE",
    fallback_api_key: Optional[str] = None,
) -> dict:
    """Blend 2–5 images (hosted URLs or data URIs) into one via Midjourney.
    Returns {"job_id", "image_url"} (the blend grid job + its image)."""
    if not 2 <= len(images) <= 5:
        raise ValueError("Blend requires between 2 and 5 images.")
    dims = (dimensions or "SQUARE").upper()
    if dims not in _BLEND_DIMENSIONS:
        dims = "SQUARE"

    async def _do(headers: dict) -> dict:
        async with httpx.AsyncClient(timeout=60) as c:
            b64_array = [await _to_data_uri(c, src) for src in images]
            resp = await c.post(
                f"{TTAPI_BASE_URL}/blend",
                json={"imgBase64Array": b64_array, "mode": mode, "dimensions": dims},
                headers=headers,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != "SUCCESS":
                raise RuntimeError(f"MJ blend failed: {body}")
            job_id = body["data"]["jobId"]
            image_url = await _poll_until_complete(c, headers, job_id)
            return {"job_id": job_id, "image_url": image_url}

    return await _with_key_fallback(api_key, fallback_api_key, "blend", _do)


async def perform_midjourney_action(
    api_key: str, job_id: str, action: str, fallback_api_key: Optional[str] = None
) -> dict:
    # Router first, when it can serve this action (U1-U4 / V1-V4 / reroll). The
    # router's MJ set has no zoom/pan/etc., so those fall straight to TTAPI.
    try:
        if _router_enabled():
            ra = _router_mj_action(action)
            if ra is not None:
                try:
                    res = await _mj_action_router(job_id, ra["action"], ra.get("position", 1))
                    if res.get("image_url"):
                        return {"job_id": res.get("job_id") or job_id, "image_url": res["image_url"]}
                except Exception as router_err:
                    logger.warning(f"⚠️ [Router] midjourney action '{action}' failed: {router_err}. Trying TTAPI…")
    except ImportError:
        pass

    ttapi_action = ACTION_MAP.get(action.upper())
    if not ttapi_action:
        raise ValueError(f"Invalid mj action '{action}'. Allowed: {sorted(ACTION_MAP)}")

    async def _do(headers: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(
                f"{TTAPI_BASE_URL}/action",
                json={"jobId": job_id, "action": ttapi_action},
                headers=headers,
            )
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except Exception:
                    body = None
                existing = _extract_existing_job_id(body, resp.text)
                if existing:
                    logger.info(
                        f"♻️ [MJ] action {action} on {job_id} already exists → reusing job {existing}"
                    )
                    image_url = await _poll_until_complete(c, headers, existing)
                    return {"job_id": existing, "image_url": image_url}
                raise RuntimeError(
                    f"MJ action HTTP {resp.status_code}: {body if body is not None else resp.text}"
                )
            body = resp.json()
            if body.get("status") != "SUCCESS":
                existing = _extract_existing_job_id(body, None)
                if existing:
                    logger.info(
                        f"♻️ [MJ] action {action} on {job_id} already exists → reusing job {existing}"
                    )
                    image_url = await _poll_until_complete(c, headers, existing)
                    return {"job_id": existing, "image_url": image_url}
                raise RuntimeError(f"MJ action failed: {body}")
            new_job_id = body["data"]["jobId"]
            image_url = await _poll_until_complete(c, headers, new_job_id)
            return {"job_id": new_job_id, "image_url": image_url}

    return await _with_key_fallback(api_key, fallback_api_key, f"action {action}", _do)
