"""Unit tests for the pure helpers (run with the CLI's test venv, see README)."""

from backend.api.core.camera_prompt import build_camera_prompt
from backend.api.core.catalog import clamp_duration, fit_aspect_ratio, resolve_image_model, video_model
from backend.api.core.timeline import absolute_captions, output_size, plan_scenes
from backend.api.routes.story import PlanRequest, ReferenceIn, normalize_plan, plan_prompt


def test_absolute_captions_merges_scene_and_timed():
    doc = {
        "scenes": [{"duration": 2, "caption": "first"}, {"duration": 3, "caption": ""}, {"duration": 1, "caption": "last"}],
        "captions": [{"text": "timed", "start": 2.5, "end": 3.5}],
    }
    caps = absolute_captions(doc)
    assert [c["text"] for c in caps] == ["first", "timed", "last"]
    assert caps[-1]["start"] == 5 and caps[-1]["end"] == 6


def test_plan_scenes_requires_media():
    assets = {"a": {"type": "image", "url": "x"}}
    specs = plan_scenes({"scenes": [{"assetId": "a", "duration": 3}]}, assets)
    assert specs[0]["kind"] == "image" and specs[0]["duration"] == 3
    try:
        plan_scenes({"scenes": [{"assetId": "missing"}]}, assets)
    except ValueError as e:
        assert "Scene 1" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_output_size_short_side():
    assert output_size("16:9", "720p") == (1280, 720)
    assert output_size("9:16", "1080p") == (1080, 1920)


def test_clamp_duration_to_engine_range():
    assert clamp_duration("kling_3_0_pro", 30) == 15
    assert clamp_duration("seedance_2_0", 2) == 4
    assert clamp_duration("ltx_video", 7) in (6, 8)


def test_image_model_resolution():
    assert resolve_image_model("auto") == "nano_banana_pro"
    assert resolve_image_model("unknown") == "nano_banana_pro"
    assert resolve_image_model("midjourney") == "midjourney"


def test_normalize_plan_fits_total_and_engine():
    data = {"scenes": [{"title": "A", "duration": 20, "image_prompt": "a"}, {"title": "B", "duration": 1, "image_prompt": "b", "references": [1, 9]}]}
    scenes = normalize_plan(data, 20, "kling_3_0_standard", {1})
    assert sum(s["duration"] for s in scenes) == 20
    assert all(3 <= s["duration"] <= 15 for s in scenes)
    assert scenes[1]["reference_indices"] == [1]


def test_camera_prompt_preset_and_custom():
    assert "dolly-in" in build_camera_prompt("Dolly in", [], 6)
    custom = build_camera_prompt("Custom path", [
        {"time": 0, "orbit": 0, "elevation": 0, "distance": 1},
        {"time": 100, "orbit": 90, "elevation": 20, "distance": 0.6},
    ], 6, "Keep it slow.")
    assert "orbit 90° to the right" in custom and "Keep it slow." in custom


def test_video_catalog_matches_fal_endpoints():
    assert video_model("seedance_2_0")["resolutions"] == ["480p", "720p", "1080p", "4k"]
    assert video_model("seedance_2_5")["id"] == "seedance_2_5"
    assert clamp_duration("seedance_2_5", 40) == 30
    for kling in ("kling_3_0_standard", "kling_3_0_pro", "kling_o3_pro"):
        assert video_model(kling)["resolutions"] == ["auto"]


def test_fit_aspect_ratio_snaps_to_model_options():
    assert fit_aspect_ratio("kling_3_0_pro", "16:9") == "16:9"
    assert fit_aspect_ratio("kling_3_0_pro", "21:9") == "16:9"
    assert fit_aspect_ratio("kling_3_0_pro", "4:3") == "1:1"
    assert fit_aspect_ratio("seedance_2_5", "4:3") == "4:3"


def test_seedance_endpoint_routing(monkeypatch):
    from backend.api.core import fal_helper

    calls = []
    monkeypatch.setattr(fal_helper, "_run_gen", lambda key, name, model_id, prompt, **kw: calls.append((model_id, kw)))
    fal_helper.generate_video("k", "seedance_2_0", "p", resolution="720p")
    fal_helper.generate_video("k", "seedance_2_0", "p", resolution="4k")
    fal_helper.generate_video("k", "seedance_2_0", "p", resolution="1080p", start_image="http://x/a.png")
    fal_helper.generate_video("k", "seedance_2_5", "p", image_references=["http://x/a.png"])
    fal_helper.generate_video("k", "seedance_2_5", "p", start_image="http://x/a.png", aspect_ratio="16:9")
    assert calls[0][0] == "bytedance/seedance-2.0/fast/text-to-video"
    assert calls[1][0] == "bytedance/seedance-2.0/text-to-video"
    assert calls[2][0] == "bytedance/seedance-2.0/image-to-video"
    assert calls[3][0] == "bytedance/seedance-2.5/reference-to-video" and calls[3][1]["image_urls"] == ["http://x/a.png"]
    assert calls[4][0] == "bytedance/seedance-2.5/image-to-video" and calls[4][1]["aspect_ratio"] == "auto"


def test_kling_4_payloads(monkeypatch):
    from backend.api.core import fal_helper
    from backend.api.core.catalog import clamp_duration, fit_aspect_ratio

    assert clamp_duration("kling_4_0", 40) == 30 and clamp_duration("kling_4_0", 2) == 3
    assert fit_aspect_ratio("kling_4_0", "21:9") == "21:9"
    calls = []
    monkeypatch.setattr(fal_helper, "_run_gen", lambda key, name, model_id, prompt, **kw: calls.append(
        (model_id, fal_helper.build_payload(name, prompt, **kw))))
    fal_helper.generate_video("k", "kling_4_0", "p", duration="12", aspect_ratio="9:16", resolution="720p", audio=False)
    fal_helper.generate_video("k", "kling_4_0", "p", start_image="http://x/a.png", end_image="http://x/b.png",
                              duration="8", aspect_ratio="16:9", elements=[{"frontal_image": "http://x/c.png"}])
    t2v, i2v = calls
    assert t2v[0] == "kling/v4/text-to-video"
    assert t2v[1] == {"duration": 12, "aspect_ratio": "9:16", "resolution": "720p", "prompt": "p"}
    assert i2v[0] == "kling/v4/image-to-video"
    assert i2v[1]["start_image_url"] == "http://x/a.png" and i2v[1]["end_image_url"] == "http://x/b.png"
    assert i2v[1]["duration"] == 8 and "aspect_ratio" not in i2v[1] and "elements" not in i2v[1]
    assert not {"negative_prompt", "generate_audio"} & set(i2v[1])


def test_runware_latest_video_payloads(monkeypatch):
    import types

    from backend.api.core import runware_helper as rw

    assert rw.get_runware_video_model("kling_4_0") == "klingai:kling-video@4.0"
    assert rw.get_runware_video_model("seedance_2_5") == "bytedance:seedance@2.5"
    assert rw.aspect_ratio_to_video_dimensions("21:9", "klingai:kling-video@4.0", "1080p") == (2200, 940)
    assert rw.aspect_ratio_to_video_dimensions("1:1", "klingai:kling-video@4.0", "720p") == (940, 940)
    assert rw.aspect_ratio_to_video_dimensions("4:3", "bytedance:seedance@2.5", "480p") == (752, 560)
    assert rw._resolve_duration("klingai:kling-video@4.0", "25") == 25.0
    assert rw._resolve_duration("bytedance:seedance@2.5", "2") == 4.0

    import runware
    sent = []

    class FakeRunware:
        def __init__(self, api_key): pass
        async def connect(self): pass
        async def videoInference(self, requestVideo):
            sent.append(requestVideo)
            return [types.SimpleNamespace(videoURL="http://v/out.mp4")]

    monkeypatch.setattr(runware, "Runware", FakeRunware)
    import anyio

    url = anyio.run(lambda: rw.generate_video_runware(
        "key", "bytedance:seedance@2.5", "p", "9:16", "12", resolution="1080p",
        start_image="http://x/a.png", end_image="http://x/b.png", audio=False))
    assert url == "http://v/out.mp4"
    req = sent[0]
    assert (req.width, req.height, req.duration) == (1080, 1920, 12)
    assert [(f.inputImage, f.frame) for f in req.frameImages] == [("http://x/a.png", "first"), ("http://x/b.png", "last")]
    assert req.settings.audio is False
    anyio.run(lambda: rw.generate_video_runware("key", "klingai:kling-video@4.0", "p", "16:9", "40", audio=True))
    assert (sent[1].width, sent[1].height, sent[1].duration) == (1280, 720, 30) and sent[1].settings is None


def test_plan_prompt_pins_first_and_last_frames_and_flow():
    refs = [ReferenceIn(index=1, asset_id="a", analysis={"kind": "character", "name": "boy", "description": "a boy on a bike"}),
            ReferenceIn(index=2, asset_id="b", analysis={"kind": "background", "name": "finish", "description": "a park gate"})]
    body = PlanRequest(prompt="a boy is cycling", total_duration=10, first_frame=1, last_frame=2, flow=True)
    text = plan_prompt(body, 10, list(range(3, 16)), refs)
    assert "OPENS exactly on reference image 1" in text
    assert "ENDS exactly on reference image 2" in text
    assert "ONE continuous shot" in text
    # Without frames or flow, none of the rules are added; unknown indices are ignored.
    plain = plan_prompt(PlanRequest(prompt="x", first_frame=9), 10, [5, 10], [])
    assert "OPENS" not in plain and "continuous shot" not in plain

