"""Server-side export of a project timeline to MP4 (ffmpeg).

Mirrors the browser preview/`lib/palette/render.ts` semantics:
  * each scene shows its media fitted into the frame on #0c0c0c, scaled by
    `scene.scale`, offset by `scene.position` (fractions of the frame),
  * `Push in` zooms 12% over the scene, `Pan left` slides 45px (at 720p),
  * `Fade` fades the scene in over its first eighth (max 0.5s),
  * video clips play from `sourceIn` for `duration`, their audio at the
    project's source volume; stills get a silent track,
  * captions: project timed captions plus per-scene captions (absolute time),
  * voice + music tracks mixed at the project's audio-mix volumes, music
    looped to length.

All functions here are blocking — call them through `anyio.to_thread`.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

from backend.api.core.video_merge import _CAPTION_FONT, dimensions_for, has_audio_stream

logger = logging.getLogger("pltt_video_studio_2.timeline")

# Noto Sans KR covers Latin and Hangul (captions follow the platform
# language); DejaVu is the fallback if the bundled CJK font is missing.
_NOTO_KR = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansKR-Regular.otf"
CAPTION_FONT = str(_NOTO_KR) if _NOTO_KR.exists() else _CAPTION_FONT

FPS = 30
BACKGROUND = "0x0c0c0c"
_ENC = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]


def _escape_text(text: str) -> str:
    """Escape caption text for drawtext with `expansion=none` (so `%` is
    literal). Straight quotes become a typographic apostrophe because quote
    escaping inside a quoted filter argument is fragile."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")


def _even(x: float) -> int:
    return max(2, int(round(x / 2) * 2))


def output_size(ratio: str, resolution: str) -> tuple[int, int]:
    return dimensions_for(ratio or "16:9", resolution or "720p")


def _run(cmd: list[str], timeout: int, what: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg {what} failed: {(proc.stderr or '').strip()[-1200:]}")


def render_segment(
    ffmpeg: str,
    src: str,
    out: str,
    *,
    kind: str,
    width: int,
    height: int,
    duration: float,
    source_in: float = 0.0,
    scale: float = 1.0,
    position: Optional[dict] = None,
    motion: str = "Still",
    transition: str = "Cut",
    source_volume: float = 1.0,
    crf: int = 20,
) -> None:
    """Render one scene to a normalized mp4 (WxH, 30fps, stereo 48k AAC)."""
    duration = max(0.1, float(duration))
    scale = min(1.0, max(0.3, float(scale or 1.0)))
    px = float((position or {}).get("x") or 0.0) * width
    py = float((position or {}).get("y") or 0.0) * height
    box_w, box_h = _even(width * scale), _even(height * scale)
    unit = min(width, height) / 720
    pan = 45 * unit if motion == "Pan left" else 0.0

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if kind == "image":
        cmd += ["-loop", "1", "-t", f"{duration:.3f}", "-i", src]
    else:
        cmd += ["-ss", f"{max(0.0, source_in):.3f}", "-t", f"{duration:.3f}", "-i", src]
    cmd += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    parts = [
        f"color=c={BACKGROUND}:s={width}x{height}:r={FPS}:d={duration:.3f}[bg]",
        f"[0:v]fps={FPS},scale={box_w}:{box_h}:force_original_aspect_ratio=decrease,setsar=1[m]",
        (
            f"[bg][m]overlay=x='(W-w)/2+{px:.2f}+(1-t/{duration:.3f})*{pan:.2f}':"
            f"y='(H-h)/2+{py:.2f}':eval=frame:shortest=1[ov]"
        ),
    ]
    chain = "[ov]"
    if motion == "Push in":
        frames = max(1, int(duration * FPS))
        parts.append(
            f"{chain}zoompan=z='1+0.12*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={width}x{height}:fps={FPS}[zp]"
        )
        chain = "[zp]"
    if transition == "Fade":
        parts.append(f"{chain}fade=t=in:st=0:d={min(0.5, duration / 8):.3f}[fd]")
        chain = "[fd]"
    parts.append(f"{chain}format=yuv420p,trim=duration={duration:.3f}[v]")

    use_source_audio = kind == "video" and has_audio_stream(ffmpeg, src)
    if use_source_audio:
        parts.append(
            f"[0:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={max(0.0, source_volume):.3f},apad,atrim=duration={duration:.3f}[a]"
        )
        audio = "[a]"
    else:
        audio = "1:a"

    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", audio]
    cmd += _ENC + ["-crf", str(crf), "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-t", f"{duration:.3f}", "-movflags", "+faststart", out]
    _run(cmd, 600, "scene render")


def concat_segments(ffmpeg: str, paths: list[str], out: str) -> None:
    """Join normalized segments (identical codecs) without re-encoding."""
    if not paths:
        raise ValueError("nothing to concatenate")
    listing = Path(out).with_suffix(".txt")
    listing.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in paths))
    _run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", "-movflags", "+faststart", out],
        600, "concat",
    )


def mux_mix(
    ffmpeg: str,
    video: str,
    out: str,
    *,
    total: float,
    voice: Optional[str] = None,
    music: Optional[str] = None,
    voice_volume: float = 1.0,
    music_volume: float = 0.5,
    captions: Optional[list[dict]] = None,
    crf: int = 20,
) -> None:
    """Mix voice/music over the concatenated video (whose own audio already
    carries source audio at its volume) and burn captions."""
    captions = [c for c in (captions or []) if (c.get("text") or "").strip() and c["end"] > c["start"]]
    # A track without an audio stream (e.g. a silent video picked as music)
    # would break the filter graph; drop it instead.
    voice = voice if voice and has_audio_stream(ffmpeg, voice) else None
    music = music if music and has_audio_stream(ffmpeg, music) else None
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", video]
    idx = 1
    voice_idx = music_idx = None
    if voice:
        cmd += ["-i", voice]
        voice_idx, idx = idx, idx + 1
    if music:
        cmd += ["-stream_loop", "-1", "-i", music]
        music_idx, idx = idx, idx + 1

    parts: list[str] = []
    if captions:
        draws = ",".join(
            "drawtext=fontfile={font}:expansion=none:text='{text}':fontsize=h/22:fontcolor=white:"
            "box=1:boxcolor=0x101214@0.86:boxborderw=14:x=(w-text_w)/2:y=h-th-h/9:"
            "enable='between(t,{s:.2f},{e:.2f})'".format(
                font=CAPTION_FONT, text=_escape_text(c["text"].replace("\n", " ")[:126]),
                s=max(0.0, c["start"]), e=max(0.0, c["end"]),
            )
            for c in captions
        )
        parts.append(f"[0:v]{draws}[vout]")
        vmap = "[vout]"
    else:
        vmap = "0:v"

    mix_inputs = ["[0:a]"]
    if voice_idx is not None:
        parts.append(f"[{voice_idx}:a]aresample=48000,volume={max(0.0, voice_volume):.3f},apad[vo]")
        mix_inputs.append("[vo]")
    if music_idx is not None:
        parts.append(f"[{music_idx}:a]aresample=48000,volume={max(0.0, music_volume):.3f}[mu]")
        mix_inputs.append("[mu]")
    fade_start = max(0.0, total - 0.4)
    if len(mix_inputs) > 1:
        parts.append(
            f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0:normalize=0,"
            f"afade=t=out:st={fade_start:.3f}:d=0.4[aout]"
        )
    else:
        parts.append(f"[0:a]afade=t=out:st={fade_start:.3f}:d=0.4[aout]")

    cmd += ["-filter_complex", ";".join(parts), "-map", vmap, "-map", "[aout]"]
    cmd += (_ENC + ["-crf", str(crf)]) if captions else ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", out]
    _run(cmd, 900, "audio/caption mix")


def absolute_captions(doc: dict) -> list[dict]:
    """Project timed captions + per-scene captions on the absolute timeline
    (same merge as the browser's SRT export)."""
    caps: list[dict] = [
        {"text": str(c.get("text") or ""), "start": float(c.get("start") or 0), "end": float(c.get("end") or 0)}
        for c in (doc.get("captions") or [])
    ]
    at = 0.0
    for s in doc.get("scenes") or []:
        d = float(s.get("duration") or 0)
        if (s.get("caption") or "").strip():
            caps.append({"text": s["caption"], "start": at, "end": at + d})
        at += d
    return sorted(caps, key=lambda c: c["start"])


def plan_scenes(doc: dict, assets: dict[str, Any]) -> list[dict]:
    """Validated per-scene render specs. Raises ValueError with a readable
    reason when a scene can't be rendered."""
    specs = []
    for i, s in enumerate(doc.get("scenes") or []):
        a = assets.get(s.get("assetId") or "")
        if not a or a["type"] not in ("image", "video"):
            raise ValueError(f"Scene {i + 1} has no image or video")
        specs.append({
            "asset": a,
            "kind": a["type"],
            "duration": max(0.1, float(s.get("duration") or 5)),
            "source_in": float(s.get("sourceIn") or 0),
            "scale": float(s.get("scale") if s.get("scale") is not None else 1),
            "position": s.get("position") or {"x": 0, "y": 0},
            "motion": s.get("motion") or "Still",
            "transition": s.get("transition") or "Cut",
        })
    if not specs:
        raise ValueError("Add scenes first")
    return specs
