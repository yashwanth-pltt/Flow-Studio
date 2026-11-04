"""Camera path (preset + keyframes from the camera dialog) → a motion
instruction an image-to-video model follows.

Keyframes carry orbit (° around the subject, + = to the right), elevation
(° above the eye line) and distance (1 = starting framing; <1 closer).
"""

from __future__ import annotations

from typing import Any

PRESET_TEXT = {
    "Dolly in": "a smooth dolly-in toward the subject",
    "Dolly out": "a smooth dolly-out away from the subject, revealing more of the scene",
    "Spiral in": "a spiral move that circles the subject while closing in",
    "Crane up": "a crane shot rising from below eye level to a high angle",
    "Orbit 360° right": "a full 360° orbit around the subject, moving to the right",
    "Orbit 360° left": "a full 360° orbit around the subject, moving to the left",
    "Half orbit right": "a half orbit (180°) around the subject, moving to the right",
    "Half orbit left": "a half orbit (180°) around the subject, moving to the left",
    "High orbit right": "a high-angle half orbit to the right, looking down at the subject",
    "Low orbit right": "a low-angle half orbit to the right, looking up at the subject",
    "Orbit 360° spiral in": "a full 360° orbit that spirals in closer while rising",
    "Swing": "a gentle swing from left to right across the subject",
}


def _describe_leg(a: dict, b: dict) -> str:
    parts = []
    d_orbit = float(b.get("orbit", 0)) - float(a.get("orbit", 0))
    d_elev = float(b.get("elevation", 0)) - float(a.get("elevation", 0))
    d_dist = float(b.get("distance", 1)) - float(a.get("distance", 1))
    if abs(d_orbit) >= 5:
        parts.append(f"orbit {abs(round(d_orbit))}° to the {'right' if d_orbit > 0 else 'left'}")
    if abs(d_elev) >= 5:
        parts.append(f"{'rise' if d_elev > 0 else 'lower'} {abs(round(d_elev))}°")
    if abs(d_dist) >= 0.08:
        parts.append("move closer" if d_dist < 0 else "pull back")
    return ", ".join(parts) or "hold steady"


def build_camera_prompt(preset: str, keyframes: list[dict[str, Any]], duration: float, instructions: str = "") -> str:
    frames = sorted(keyframes or [], key=lambda k: float(k.get("time", 0)))
    if preset in PRESET_TEXT:
        move = PRESET_TEXT[preset]
    elif len(frames) >= 2:
        legs = []
        for a, b in zip(frames, frames[1:]):
            start = round(float(a.get("time", 0)) / 100 * duration, 1)
            end = round(float(b.get("time", 0)) / 100 * duration, 1)
            legs.append(f"from {start}s to {end}s {_describe_leg(a, b)}")
        move = "a custom camera path: " + "; ".join(legs)
    else:
        move = "a slow, steady camera move"
    prompt = (
        f"The scene stays exactly as in the first frame — same people, place, lighting and styling. "
        f"Only the camera moves: {move}, over {round(duration)} seconds, smooth and cinematic, "
        f"stable framing on the subject, natural motion blur."
    )
    extra = (instructions or "").strip()
    if extra:
        prompt += f" {extra}"
    return prompt
