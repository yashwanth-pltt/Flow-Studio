"""ffmpeg-based clip concatenation for the Long Video (story) flow.

The per-scene clips usually come from one model with identical params, but a
provider fallback (e.g. one scene served by Runware at a slightly different
size) can change a stream mid-list — so every input is normalized through a
scale/pad + fps graph before the concat instead of stream-copied. Audio is
concatenated when every clip has an audio stream; a clip without one fails the
audio graph, and the merge retries video-only.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pltt_video_studio_2.video_merge")

# Bundled font for burned captions (Bitstream Vera / DejaVu license — see
# LICENSE_DEJAVU alongside it) so drawtext never depends on the host having
# fontconfig + a default font registered.
_CAPTION_FONT = str(Path(__file__).resolve().parents[2] / "assets" / "fonts" / "DejaVuSans.ttf")


_last_ffmpeg_error: str | None = None


def ffmpeg_unavailable_reason() -> str | None:
    """Why the most recent find_ffmpeg() call returned None, for surfacing in
    API error details without needing access to server-side logs."""
    return _last_ffmpeg_error


def find_ffmpeg() -> str | None:
    """System ffmpeg first; fall back to the static binary bundled with
    imageio-ffmpeg when that package is installed."""
    global _last_ffmpeg_error
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
    except ImportError as e:
        _last_ffmpeg_error = f"imageio-ffmpeg is not installed on this host ({e})"
        logger.error(_last_ffmpeg_error)
        return None
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        _last_ffmpeg_error = f"imageio-ffmpeg could not resolve a usable ffmpeg binary ({e})"
        logger.exception(_last_ffmpeg_error)
        return None
    _last_ffmpeg_error = None
    return exe


# resolution → pixels on the SHORT side (portrait keeps 1080p = 1080 wide).
_SHORT_SIDE = {"480p": 480, "720p": 720, "1080p": 1080, "4k": 2160}


def _even(x: float) -> int:
    return max(2, int(round(x / 2) * 2))


def dimensions_for(aspect_ratio: str | None, resolution: str | None) -> tuple[int, int]:
    """Output frame size for an aspect ratio + resolution label, matching the
    providers' convention (resolution names the short side of the frame)."""
    base = _SHORT_SIDE.get((resolution or "1080p").lower(), 1080)
    try:
        wr, hr = (aspect_ratio or "16:9").split(":")
        ratio = float(wr) / float(hr)
    except Exception:
        ratio = 16 / 9
    if ratio >= 1:
        return _even(base * ratio), base
    return base, _even(base / ratio)


def merge_videos(
    ffmpeg: str,
    paths: list[str],
    out_path: str,
    width: int,
    height: int,
    fps: int = 30,
) -> None:
    """Concatenate `paths` in order into `out_path` as an H.264/AAC mp4.
    Blocking (run it in a thread). Raises RuntimeError with the tail of
    ffmpeg's stderr on failure."""

    def run(with_audio: bool) -> subprocess.CompletedProcess:
        n = len(paths)
        parts: list[str] = []
        for i in range(n):
            parts.append(
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]"
            )
            if with_audio:
                parts.append(
                    f"[{i}:a]aresample=48000,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
                )
        streams = "".join(
            f"[v{i}][a{i}]" if with_audio else f"[v{i}]" for i in range(n)
        )
        parts.append(
            f"{streams}concat=n={n}:v=1:a={1 if with_audio else 0}[v]"
            + ("[a]" if with_audio else "")
        )
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        for p in paths:
            cmd += ["-i", p]
        cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
        if with_audio:
            cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            out_path,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=900)

    proc = run(with_audio=True)
    if proc.returncode != 0:
        logger.warning(
            "ffmpeg concat with audio failed (%s); retrying video-only",
            (proc.stderr or "").strip()[-300:],
        )
        proc = run(with_audio=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg concat failed: {(proc.stderr or '').strip()[-1000:]}"
        )


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_duration(ffmpeg: str, path: str) -> float:
    """Real duration of a media file in seconds, read off ffmpeg's own stderr
    banner rather than a standalone ffprobe binary — imageio-ffmpeg's bundled
    static build only ships ffmpeg, not ffprobe. Returns 0.0 if it can't be
    parsed. Blocking (run it in a thread)."""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", path],
        capture_output=True, text=True, timeout=30,
    )
    m = _DURATION_RE.search(proc.stderr or "")
    if not m:
        return 0.0
    hours, minutes, seconds = m.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _escape_drawtext(text: str) -> str:
    """Escapes text for ffmpeg's drawtext filter argument. Straight quotes are
    swapped for a typographic apostrophe rather than escaped — drawtext's
    quote-escaping inside an already-quoted filter arg is notoriously fragile,
    and captions never need a literal `'`."""
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("%", "\\%")
        .replace("'", "’")
    )


def mux_final_audio(
    ffmpeg: str,
    video_path: str,
    out_path: str,
    duration_seconds: float,
    narration_path: Optional[str] = None,
    music_path: Optional[str] = None,
    captions: Optional[list[dict]] = None,
    narration_rate: float = 1.0,
) -> None:
    """Mixes optional narration/music tracks onto `video_path` (expected
    silent — the scene clips it was concatenated from had model-native audio
    disabled for these modes) and optionally burns caption lines via
    drawtext, writing an H.264/AAC mp4 to `out_path`.

    `duration_seconds` pins the output length to the video's own runtime
    (`-t`) so narration running long never extends it and narration running
    short just trails into padded silence, rather than either track's length
    dictating the final video's duration.

    `narration_rate` (caller-computed from the narration's actual measured
    length vs. the time budget it needs to fit) speeds up ONLY the narration
    track via `atempo` — e.g. 1.1 plays it 10% faster — so a script that runs
    a little long finishes naturally instead of getting chopped off by the
    final `-t` trim below. The mixed output also always gets a short tail
    fade-out, so even a narration still too long after that (or any other
    track) ends smoothly rather than an abrupt digital cutoff. Blocking (run
    it in a thread). Raises RuntimeError with the tail of ffmpeg's stderr on
    failure."""
    if not narration_path and not music_path and not captions:
        raise ValueError("mux_final_audio needs at least one of narration/music/captions")

    def _run(use_sidechain: bool) -> subprocess.CompletedProcess:
        inputs = [video_path]
        audio_sources: list[tuple[str, int]] = []  # (kind, input index)
        if narration_path:
            inputs.append(narration_path)
            audio_sources.append(("narration", len(inputs) - 1))
        if music_path:
            inputs.append(music_path)
            audio_sources.append(("music", len(inputs) - 1))

        filter_parts: list[str] = []

        if captions:
            vf = "[0:v]" + ",".join(
                "drawtext=fontfile={font}:text='{text}':fontsize=42:fontcolor=white:"
                "borderw=3:bordercolor=black@0.85:x=(w-text_w)/2:y=h-th-60:"
                "enable='between(t,{start:.2f},{end:.2f})'".format(
                    font=_CAPTION_FONT,
                    text=_escape_drawtext(c["text"]),
                    start=max(0.0, c["start"] or 0.0),
                    end=max(0.0, c["end"] or 0.0),
                )
                for c in captions
            ) + "[vout]"
            filter_parts.append(vf)
            video_map = "[vout]"
        else:
            video_map = None  # stream-copied below — nothing touches the pixels.

        narration_label: Optional[str] = None
        music_label: Optional[str] = None
        for kind, idx in audio_sources:
            # Narration plays at full presence throughout. Music plays at a
            # normal listening level here — when narration is ALSO present, that
            # level only matters for the gaps between/after narration when
            # ducking dynamically (use_sidechain), or all the time as a flat
            # background level in the static fallback below.
            if kind == "narration":
                vol = "1.0"
            elif use_sidechain:
                vol = "0.8" if narration_path else "0.9"
            else:
                # Static fallback (no sidechaincompress): hold music low the
                # whole way through whenever narration is present, since
                # there's no real-time detector to duck it only on demand.
                vol = "0.25" if narration_path else "0.9"
            label = f"trk{idx}"
            # atempo only touches the narration stream — speeding up music too
            # would shift its pitch/feel for no reason, and music's own length is
            # sized to the run's duration already (see generate_music's
            # duration_ms).
            tempo = f"atempo={narration_rate:.4f}," if kind == "narration" and narration_rate != 1.0 else ""
            # `apad` extends short tracks with silence so a track shorter than
            # `duration_seconds` doesn't leave amix/the final `-t` trim exposing
            # nothing — the final `-t` below still does the authoritative cut.
            filter_parts.append(f"[{idx}:a]{tempo}volume={vol},apad[{label}]")
            if kind == "narration":
                narration_label = label
            else:
                music_label = label

        if narration_label and music_label and use_sidechain:
            # Real-time ducking: sidechaincompress watches the narration track's
            # actual level and pulls music down WHILE it's genuinely speaking —
            # attack is fast so it ducks almost the instant narration starts,
            # release is slow enough not to pump between individual words but
            # still lets music climb back to its normal (`vol=0.8` above) level
            # during real gaps: between sentences, and for however long the
            # video keeps going after narration itself ends.
            #
            # The narration track feeds TWO downstream filters here (the
            # sidechain detector AND the final mix) — asplit forks it into two
            # independent copies for that rather than referencing the same
            # link label twice. Confirmed against a real ffmpeg build: reusing
            # one label as two filters' input, alongside the untouched video
            # stream this whole graph sits next to (-map 0:v, never itself
            # filtered), silently breaks the graph ("Stream specifier ...
            # matches no streams") — asplit sidesteps it entirely.
            filter_parts.append(f"[{narration_label}]asplit=2[{narration_label}_sc][{narration_label}_mix]")
            filter_parts.append(
                f"[{music_label}][{narration_label}_sc]sidechaincompress="
                f"threshold=0.05:ratio=15:attack=15:release=400:makeup=1[music_ducked]"
            )
            filter_parts.append(
                f"[music_ducked][{narration_label}_mix]amix=inputs=2:"
                f"duration=longest:dropout_transition=0:normalize=0[amixout]"
            )
            audio_map = "[amixout]"
        elif narration_label and music_label:
            # Static fallback mix — flat pre-set volumes above, just combined.
            filter_parts.append(
                f"[{narration_label}][{music_label}]amix=inputs=2:"
                f"duration=longest:dropout_transition=0:normalize=0[amixout]"
            )
            audio_map = "[amixout]"
        else:
            audio_map = f"[{narration_label or music_label}]"

        # Tail fade so the `-t` trim below (or a track just running to its own
        # natural end) never reads as an abrupt cutoff — a plain digital edge is
        # far more noticeable on speech than 0.4s of fade.
        fade_start = max(0.0, duration_seconds - 0.4)
        filter_parts.append(f"{audio_map}afade=t=out:st={fade_start:.3f}:d=0.4[afinal]")
        audio_map = "[afinal]"

        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        for p in inputs:
            cmd += ["-i", p]
        cmd += ["-filter_complex", ";".join(filter_parts)]
        cmd += ["-map", video_map or "0:v"]
        if video_map:
            cmd += [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                "-pix_fmt", "yuv420p",
            ]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-map", audio_map, "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-t", f"{duration_seconds:.3f}", "-movflags", "+faststart", out_path]

        return subprocess.run(cmd, capture_output=True, text=True, timeout=900)

    proc = _run(use_sidechain=True)
    if proc.returncode != 0 and narration_path and music_path:
        # sidechaincompress can be missing/misbehaving on some ffmpeg builds —
        # retry once with the flat-volume fallback so narration+music still
        # ship mixed (just without dynamic ducking) instead of failing outright.
        logger.warning(
            "⚠️ sidechaincompress mux failed, retrying with static-volume mix: %s",
            (proc.stderr or "").strip()[-500:],
        )
        proc = _run(use_sidechain=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg audio/caption mux failed: {(proc.stderr or '').strip()[-1000:]}"
        )


def extract_first_frame(ffmpeg: str, video_path: str, out_path: str) -> None:
    """Grab the video's very first decodable frame as a JPEG at `out_path`.
    Blocking (run it in a thread). Raises RuntimeError with the tail of
    ffmpeg's stderr on failure."""
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path, "-frames:v", "1", "-q:v", "2",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg thumbnail extraction failed: {(proc.stderr or '').strip()[-1000:]}"
        )


def extract_last_frame(ffmpeg: str, video_path: str, out_path: str) -> None:
    """Grab the video's LAST frame as a JPEG at `out_path` — the start frame of the next clip in a continuous
    (Flow) film. Seeks to just before the end; falls back to the measured duration. Blocking (run it in a thread).
    Raises RuntimeError with the tail of ffmpeg's stderr on failure."""
    def run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True,
                              text=True, timeout=60)

    proc = run(["-sseof", "-0.25", "-i", video_path, "-update", "1", "-q:v", "2", out_path])
    if proc.returncode == 0 and Path(out_path).exists() and Path(out_path).stat().st_size > 0:
        return
    duration = probe_duration(ffmpeg, video_path) or 0.0
    proc = run(["-ss", f"{max(0.0, duration - 0.1):.3f}", "-i", video_path, "-frames:v", "1", "-q:v", "2", out_path])
    if proc.returncode != 0 or not Path(out_path).exists():
        raise RuntimeError(f"ffmpeg last-frame extraction failed: {(proc.stderr or '').strip()[-1000:]}")


# scale-to-COVER + center-crop: fills the whole WxH frame with no black
# bars, cropping whatever overflows — used for anything the user uploads
# directly (image-to-video, uploaded-video re-encode below), so it always
# fills the frame edge-to-edge exactly like every generated clip does,
# regardless of the source file's own aspect ratio. (merge_videos' own
# per-clip normalization intentionally still PADS instead — that's for
# minor size drift between provider outputs, not a real aspect mismatch.)
def _crop_to_fill_filter(width: int, height: int, fps: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps}"
    )


def image_to_video(
    ffmpeg: str,
    image_path: str,
    out_path: str,
    width: int,
    height: int,
    duration: float,
    fps: int = 30,
) -> None:
    """Loops a still image into a short H.264/AAC mp4 of exactly `duration`
    seconds — for a scene the user adds directly (e.g. a closing "thank you"
    card) rather than generating. Cropped to fill WxH exactly (see
    _crop_to_fill_filter) so an image shot in a different aspect ratio than
    the run still fills the frame rather than being letterboxed, and given a
    SILENT audio track (not no audio track at all) — merge_videos' concat
    falls back to dropping audio from every clip the moment even one input
    lacks an audio stream entirely, which would silently mute an otherwise
    fully-narrated/scored video. Blocking (run it in a thread). Raises
    RuntimeError with the tail of ffmpeg's stderr on failure."""
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-i", image_path,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{max(0.1, duration):.3f}",
        "-vf", _crop_to_fill_filter(width, height, fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg image-to-video failed: {(proc.stderr or '').strip()[-1000:]}"
        )


def has_audio_stream(ffmpeg: str, path: str) -> bool:
    """Whether `path` has at least one audio stream, read off ffmpeg's own
    stderr banner (same technique as probe_duration — no standalone ffprobe
    binary needed)."""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", path],
        capture_output=True, text=True, timeout=30,
    )
    return "Audio:" in (proc.stderr or "")


def reencode_scene_video(
    ffmpeg: str,
    video_path: str,
    out_path: str,
    width: int,
    height: int,
    fps: int = 30,
) -> None:
    """Re-encodes an uploaded video (Animate's "add scene") to fill WxH
    exactly via center-crop (see _crop_to_fill_filter) — so it visually
    matches every other scene regardless of what aspect ratio it was shot
    in, the same treatment image_to_video gives an uploaded image. Keeps
    the source's own audio if it has one; synthesizes a silent track if it
    doesn't (same reasoning as image_to_video — a clip with NO audio stream
    at all forces merge_videos to drop audio from every other clip too).
    Blocking (run it in a thread). Raises RuntimeError with the tail of
    ffmpeg's stderr on failure."""
    vf = f"[0:v]{_crop_to_fill_filter(width, height, fps)}[v]"
    if has_audio_stream(ffmpeg, video_path):
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", video_path,
            "-filter_complex", vf, "-map", "[v]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_path,
        ]
    else:
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", video_path,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-filter_complex", vf, "-map", "[v]", "-map", "1:a", "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_path,
        ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg scene-video re-encode failed: {(proc.stderr or '').strip()[-1000:]}"
        )
