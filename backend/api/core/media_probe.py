"""Read duration / frame size / a poster frame from media bytes via ffmpeg."""

from __future__ import annotations

import functools
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import anyio

from backend.api.core.video_merge import extract_first_frame, find_ffmpeg, probe_duration

logger = logging.getLogger("pltt_video_studio_2.media_probe")

_SIZE_RE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")


def _frame_size(ffmpeg: str, path: str) -> tuple[Optional[int], Optional[int]]:
    proc = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True, timeout=30)
    m = _SIZE_RE.search(proc.stderr or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _probe_file(path: str, kind: str) -> dict[str, Any]:
    ffmpeg = find_ffmpeg()
    out: dict[str, Any] = {"duration": None, "width": None, "height": None, "poster": None}
    if not ffmpeg:
        return out
    try:
        if kind in ("video", "audio"):
            d = probe_duration(ffmpeg, path)
            out["duration"] = round(d, 3) if d else None
        if kind in ("video", "image"):
            out["width"], out["height"] = _frame_size(ffmpeg, path)
        if kind == "video":
            poster = str(Path(path).with_suffix(".poster.jpg"))
            extract_first_frame(ffmpeg, path, poster)
            out["poster"] = Path(poster).read_bytes()
    except Exception as exc:  # probing is best-effort
        logger.warning("media probe failed: %s", exc)
    return out


async def probe_bytes(data: bytes, suffix: str, kind: str) -> dict[str, Any]:
    """Probe in-memory media. Returns duration (s), width, height and, for
    video, the first frame as JPEG bytes under `poster`."""
    with tempfile.TemporaryDirectory(prefix="pvs-probe-") as tmp:
        path = str(Path(tmp) / f"media{suffix or '.bin'}")
        Path(path).write_bytes(data)
        return await anyio.to_thread.run_sync(functools.partial(_probe_file, path, kind))


async def probe_path(path: str, kind: str) -> dict[str, Any]:
    return await anyio.to_thread.run_sync(functools.partial(_probe_file, path, kind))
