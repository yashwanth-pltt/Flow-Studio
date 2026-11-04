"""ElevenLabs narration (TTS) + music generation for the Long Video audio
pipeline. Narration (`narrate`) tries the platform's ElevenLabs voice first
(`ctx.media.speech`, fal-ai/elevenlabs/tts/turbo-v2.5, on the org's budget)
with the chosen voice, then the app's own ELEVEN_API_KEY: eleven_v3 via the
`/with-timestamps` endpoint so the per-character alignment can be folded into
word-level caption cues. Music stays on ElevenLabs (the Eleven Music API,
`music_v2`), sized to the run's total duration.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.api.core import platform_models
from backend.api.core.media_probe import probe_bytes
from backend.api.core.std_logs import call, size

logger = logging.getLogger("pltt_video_studio_2.elevenlabs")

_BASE = "https://api.elevenlabs.io/v1"

# A widely-used ElevenLabs premade voice ("Rachel") — a sensible default when
# the user hasn't picked one in the audio settings dialog.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

#: ElevenLabs premade voices offered when the app has no ElevenLabs key of its own (the platform speaks them by name).
PLATFORM_VOICES = ["Rachel", "Aria", "Sarah", "Laura", "Charlotte", "Alice", "Matilda", "Jessica", "Lily",
                   "Roger", "Charlie", "George", "Callum", "River", "Liam", "Will", "Eric", "Chris", "Brian",
                   "Daniel", "Bill"]

_MUSIC_MIN_MS = 3000
_MUSIC_MAX_MS = 600000


async def generate_narration(
    api_key: str,
    text: str,
    voice_id: str = DEFAULT_VOICE_ID,
    language_code: Optional[str] = None,
) -> Tuple[bytes, List[Dict[str, Any]]]:
    """Returns (mp3 bytes, word-level caption cues: [{"text", "start", "end"}]).

    `language_code` (ISO 639-1, e.g. "en"/"ko") forces the pronunciation/
    accent ElevenLabs' multilingual model uses — left unset falls back to its
    own language auto-detection from the text."""
    body: Dict[str, Any] = {"text": text, "model_id": "eleven_v3"}
    if language_code:
        body["language_code"] = language_code
    async with httpx.AsyncClient(timeout=120) as client, call(
        "elevenlabs", "tts", model="eleven_v3", chars=size(text), language=language_code
    ) as _log:
        r = await client.post(
            f"{_BASE}/text-to-speech/{voice_id}/with-timestamps",
            headers={"xi-api-key": api_key},
            json=body,
        )
        _log["http_status"] = r.status_code
        r.raise_for_status()
        data = r.json()
        _log["audio_b64_chars"] = size(data.get("audio_base64")) if isinstance(data, dict) else None

    audio = base64.b64decode(data["audio_base64"])
    alignment = data.get("alignment") or data.get("normalized_alignment") or {}
    words = _words_from_alignment(
        alignment.get("characters") or [],
        alignment.get("character_start_times_seconds") or [],
        alignment.get("character_end_times_seconds") or [],
    )
    return audio, words


def tts_available(ctx: Any, api_key: Optional[str]) -> bool:
    """A voice-over can be made: the platform lists its ElevenLabs voice (cached catalog), or the app has a key."""
    return bool(api_key) or platform_models.media_listed(ctx, platform_models.PLATFORM_TTS)


async def narrate(
    ctx: Any,
    api_key: Optional[str],
    text: str,
    voice_id: Optional[str] = None,
    language_code: Optional[str] = None,
) -> Tuple[bytes, List[Dict[str, Any]], str]:
    """(mp3 bytes, word cues, provider): the platform's ElevenLabs voice first when its catalog lists it — the
    chosen voice (id or premade name), else Rachel — then ElevenLabs on the app's own key."""
    errors: List[str] = []
    if await platform_models.media_available(ctx, platform_models.PLATFORM_TTS):
        try:
            audio, words = await _platform_narration(ctx, text, voice_id, language_code)
            return audio, words, f"platform:{platform_models.PLATFORM_TTS}"
        except Exception as e:  # noqa: BLE001 — the app's own key next
            errors.append(f"platform: {e}")
            logger.warning("platform voice failed: %s", e)
    if api_key:
        try:
            audio, words = await generate_narration(api_key, text, voice_id or DEFAULT_VOICE_ID, language_code)
            return audio, words, "elevenlabs:eleven_v3"
        except Exception as e:  # noqa: BLE001
            errors.append(f"elevenlabs: {e}")
    raise RuntimeError("; ".join(errors) or "No voice is configured (set ELEVEN_API_KEY).")


async def _platform_narration(
    ctx: Any, text: str, voice_id: Optional[str], language_code: Optional[str]
) -> Tuple[bytes, List[Dict[str, Any]]]:
    voice = voice_id or platform_models.PLATFORM_DEFAULT_VOICE
    extra: Dict[str, Any] = {"timestamps": True}
    if language_code:
        extra["language_code"] = language_code
    async with call("ctx.media", "tts", model=platform_models.PLATFORM_TTS, chars=size(text),
                    language=language_code) as _log:
        try:
            result = await platform_models.platform_speech(ctx, text, voice=voice, **extra)
        except platform_models.PlatformUnavailable:
            raise
        except Exception:
            # timestamps / language_code aren't confirmed on fal's ElevenLabs endpoint: retry with the bare call
            # (word cues are then spread over the clip) rather than losing the platform voice
            extra = {}
            result = await platform_models.platform_speech(ctx, text, voice=voice)
        url = platform_models.result_url(result, "audio", "audio_url")
        if not url:
            raise platform_models.PlatformUnavailable("the platform voice returned no audio")
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
        audio = r.content
        if not audio:
            raise platform_models.PlatformUnavailable("the platform voice returned no audio")
        _log["bytes"] = size(audio)
    words = _platform_words(result.get("timestamps"))
    if not words:
        words = _even_words(text, await _audio_seconds(audio))
    return audio, words


def _platform_words(stamps: Any) -> List[Dict[str, Any]]:
    """Word cues from the platform voice's timestamps: ElevenLabs' character alignment, or word rows."""
    if isinstance(stamps, list) and len(stamps) == 1 and isinstance(stamps[0], dict) and "characters" in stamps[0]:
        stamps = stamps[0]
    if isinstance(stamps, dict):
        return _words_from_alignment(
            stamps.get("characters") or [],
            stamps.get("character_start_times_seconds") or [],
            stamps.get("character_end_times_seconds") or [],
        )
    out: List[Dict[str, Any]] = []
    for row in stamps if isinstance(stamps, list) else []:
        if not isinstance(row, dict):
            continue
        word = str(row.get("text") or row.get("word") or "").strip()
        start, end = row.get("start"), row.get("end")
        if word and isinstance(start, (int, float)) and isinstance(end, (int, float)):
            out.append({"text": word, "start": float(start), "end": float(end)})
    return out


async def _audio_seconds(audio: bytes) -> float:
    try:
        seconds = (await probe_bytes(audio, ".mp3", "audio")).get("duration")
        if seconds:
            return float(seconds)
    except Exception:  # noqa: BLE001 — estimate from the size instead
        pass
    return len(audio) * 8 / 128000  # the platform voice's mp3 is 128 kbps


def _even_words(text: str, seconds: float) -> List[Dict[str, Any]]:
    """Word cues spread over the clip by length, when the voice gave no timing."""
    words = text.split()
    total = sum(len(w) + 1 for w in words) or 1
    out: List[Dict[str, Any]] = []
    t = 0.0
    for w in words:
        span = seconds * (len(w) + 1) / total
        out.append({"text": w, "start": round(t, 3), "end": round(t + span, 3)})
        t += span
    return out


def _words_from_alignment(
    chars: List[str], starts: List[float], ends: List[float]
) -> List[Dict[str, Any]]:
    """Folds ElevenLabs' character-level alignment into word-level cues by
    grouping non-whitespace runs, so caption lines can be built from them."""
    words: List[Dict[str, Any]] = []
    buf = ""
    w_start: Optional[float] = None
    w_end: Optional[float] = None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if buf:
                words.append({"text": buf, "start": w_start, "end": w_end})
                buf = ""
                w_start = None
            continue
        if w_start is None:
            w_start = s
        buf += ch
        w_end = e
    if buf:
        words.append({"text": buf, "start": w_start, "end": w_end})
    return words


def group_captions(
    words: List[Dict[str, Any]], max_chars: int = 34
) -> List[Dict[str, Any]]:
    """Chunks word cues into short caption lines (~max_chars) for burning one
    line at a time, rather than a fresh line per single word."""
    lines: List[Dict[str, Any]] = []
    buf: List[Dict[str, Any]] = []
    length = 0
    for w in words:
        added = len(w["text"]) + (1 if buf else 0)
        if buf and length + added > max_chars:
            lines.append(_line_from_words(buf))
            buf = []
            length = 0
            added = len(w["text"])
        buf.append(w)
        length += added
    if buf:
        lines.append(_line_from_words(buf))
    return lines


def _line_from_words(words: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "text": " ".join(w["text"] for w in words),
        "start": words[0]["start"],
        "end": words[-1]["end"],
    }


async def generate_music(api_key: str, prompt: str, duration_ms: int) -> bytes:
    duration_ms = max(_MUSIC_MIN_MS, min(_MUSIC_MAX_MS, duration_ms))
    async with httpx.AsyncClient(timeout=180) as client, call(
        "elevenlabs", "music", model="music_v2", duration_ms=duration_ms
    ) as _log:
        r = await client.post(
            f"{_BASE}/music",
            headers={"xi-api-key": api_key},
            json={"prompt": prompt, "music_length_ms": duration_ms, "model_id": "music_v2"},
        )
        _log["http_status"] = r.status_code
        _log["bytes"] = size(r.content)
        r.raise_for_status()
        return r.content


async def list_voices(api_key: str) -> List[Dict[str, Any]]:
    """The account's available voices (premade + cloned)."""
    async with httpx.AsyncClient(timeout=30) as client, call("elevenlabs", "list_voices") as _log:
        r = await client.get(f"{_BASE}/voices", headers={"xi-api-key": api_key})
        _log["http_status"] = r.status_code
        r.raise_for_status()
        data = r.json()
        _log["voices"] = size(data.get("voices")) if isinstance(data, dict) else None
    return [
        {
            "id": v.get("voice_id"),
            "name": v.get("name"),
            "preview_url": v.get("preview_url"),
            "labels": v.get("labels") or {},
        }
        for v in data.get("voices") or []
        if v.get("voice_id")
    ]
