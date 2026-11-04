"""Flow: each clip continues from the previous clip's real last frame ("<video id>#last")."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.api.core import video_merge
from backend.api.routes import generate_video as gv


def _clip(tmp: Path) -> Path:
    """A 2 s test clip: red for the first second, blue for the last (so first ≠ last frame)."""
    ffmpeg = video_merge.find_ffmpeg()
    out = tmp / "clip.mp4"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=160x90:d=1",
                    "-f", "lavfi", "-i", "color=blue:s=160x90:d=1", "-filter_complex", "[0][1]concat=n=2:v=1",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def _mean_rgb(path: Path) -> tuple[int, int, int]:
    from PIL import Image

    img = Image.open(path).convert("RGB").resize((1, 1))
    return img.getpixel((0, 0))


@pytest.mark.skipif(not video_merge.find_ffmpeg(), reason="needs ffmpeg")
def test_the_last_frame_is_the_end_of_the_clip(tmp_path):
    clip = _clip(tmp_path)
    ffmpeg = video_merge.find_ffmpeg()
    first, last = tmp_path / "first.jpg", tmp_path / "last.jpg"
    video_merge.extract_first_frame(ffmpeg, str(clip), str(first))
    video_merge.extract_last_frame(ffmpeg, str(clip), str(last))
    r, g, b = _mean_rgb(first)
    assert r > 150 and b < 100  # red at the start
    r, g, b = _mean_rgb(last)
    assert b > 150 and r < 100  # blue at the end


def test_last_frame_refs_resolve_to_the_stored_still(monkeypatch):
    calls = []

    async def last_frame_url(ctx, vid):
        calls.append(vid)
        return "https://cdn/frames/last.jpg" if vid == "clip1" else None

    async def any_user(db, asset_id):
        return type("Row", (), {"type": "video", "url": "https://cdn/v.mp4", "poster_url": "https://cdn/poster.jpg"})() \
            if asset_id == "clip2" else None

    class Ctx:
        db = None

    monkeypatch.setattr(gv, "last_frame_url", last_frame_url)
    monkeypatch.setattr(gv, "get_asset_any_user", any_user)
    monkeypatch.setattr(gv, "provider_safe_media_url", lambda u: u)
    assert asyncio.run(gv.resolve_frame(Ctx(), "clip1#last")) == "https://cdn/frames/last.jpg"
    # no last frame (no ffmpeg / failed): the clip's poster, never nothing when the clip exists
    assert asyncio.run(gv.resolve_frame(Ctx(), "clip2#last")) == "https://cdn/poster.jpg"
    assert asyncio.run(gv.resolve_frame(Ctx(), "missing#last")) is None
    assert calls == ["clip1", "clip2", "missing"]
