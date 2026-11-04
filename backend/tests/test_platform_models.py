"""The platform's models come first: ctx.llm for text, ctx.media for images, video and voice — with the app's own
keys as fallbacks, and a person's explicit model choice still running first."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.api.core import catalog, elevenlabs_helper, llm, platform_models
from backend.api.routes import generate_image, generate_video

KEYS = ("ANTHROPIC_API_KEY", "OPENAI_KEY", "GOOGLE_AI_API_KEY", "FAL_KEY", "RUNWARE_KEY", "ELEVEN_API_KEY",
        "LLM_ROUTER_ENABLED", "LLM_ROUTER_API_KEY", "MIDJOURNEY_KEY")


def _row(mid, ok=True, reason=None, images=None, out=None, vision=True):
    limits = {}
    if images is not None:
        limits["max_images"] = images
    if out is not None:
        limits["max_output_tokens"] = out
    return {"id": mid, "kind": "chat", "available": ok, "reason": reason,
            "capabilities": ["chat", "vision"] if vision else ["chat"], "limits": limits}


def _media_row(mid, ok=True):
    return {"id": mid, "kind": "video", "available": ok, "reason": None if ok else "org_policy"}


class FakeLLM:
    live = True

    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def catalog(self):
        return self.rows

    async def chat(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        return {"text": '{"ok": true}', "model": kw["model"], "input_tokens": 10, "output_tokens": 5,
                "stop_reason": "end_turn", "truncated": False}


class FakeMedia:
    live = True

    def __init__(self, listed=()):
        self.listed, self.calls = list(listed), []

    async def catalog(self, kind=None):
        return [_media_row(m) for m in self.listed] + [_media_row("fal-ai/not-allowed", ok=False)]

    async def run(self, model, payload=None, timeout=None):
        self.calls.append(("run", model, payload))
        if "video" in model:
            return {"video": {"url": "https://fal.media/clip.mp4"}}
        return {"images": [{"url": "https://fal.media/still.png"}]}

    async def speech(self, text, **kw):
        self.calls.append(("speech", kw.get("model"), kw))
        return {"audio": {"url": "https://fal.media/voice.mp3"}}


def _ctx(llm_service=None, media=None, org=7, **secrets):
    return SimpleNamespace(llm=llm_service, media=media, organization_id=org, config={"secrets": dict(secrets)},
                           secret=lambda name: "")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    platform_models.clear_caches()
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def _fake_provider(monkeypatch, pid, calls):
    label, prov, secret, model, _call, _vision = llm.PROVIDERS[pid]

    async def call(api_key, system, user, model, **kw):
        calls.append((pid, kw))
        return {"from": pid}

    async def vision(api_key, system, user, image_b64, mime, model, **kw):
        calls.append((pid, "vision", kw))
        return {"from": pid}

    monkeypatch.setitem(llm.PROVIDERS, pid, (label, prov, secret, model, call, vision))


# --- text ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("choice", [None, "", "palette", "auto"])
def test_the_platform_leads_for_the_default_with_the_best_model_it_allows(monkeypatch, choice):
    service = FakeLLM([_row("palette-llm"), _row("claude-sonnet-5-5"),
                       _row("claude-opus-5-5", ok=False, reason="not_approved")])
    ctx = _ctx(service, ANTHROPIC_API_KEY="a")
    calls = []
    _fake_provider(monkeypatch, "claude_opus", calls)
    assert llm.provider_order(choice)[0] == "palette"
    assert asyncio.run(llm.llm_json(ctx, "sys", "hi", choice, max_tokens=8192)) == {"ok": True}
    call = service.calls[0]
    # the best allowed model, run exactly, max_tokens capped by its (default) limit
    assert call["model"] == "claude-sonnet-5-5" and call["fallback"] is False and call["max_tokens"] == 4096
    assert not calls


def test_the_callers_max_tokens_is_used_when_the_model_allows_it():
    service = FakeLLM([_row("claude-opus-5-5", out=16000)])
    asyncio.run(llm.llm_json(_ctx(service), "sys", "hi", max_tokens=8192))
    assert service.calls[0]["max_tokens"] == 8192


def test_an_explicit_choice_runs_first_then_the_platform(monkeypatch):
    assert llm.provider_order("gpt") == ["gpt", "palette", "claude_opus", "claude_sonnet", "claude_haiku", "gemini", "router"]
    service = FakeLLM([_row("claude-opus-5-5")])
    calls = []
    _fake_provider(monkeypatch, "gpt", calls)
    out = asyncio.run(llm.llm_json(_ctx(service, OPENAI_KEY="o"), "sys", "hi", "gpt"))
    assert out == {"from": "gpt"} and not service.calls


def test_pictures_go_to_a_vision_model_that_takes_them():
    rows = [_row("claude-opus-5-5", images=0), _row("gpt-5.5", images=8), _row("palette-llm", vision=False)]
    assert platform_models.pick(rows, 1)[0] == "gpt-5.5"
    assert platform_models.pick([_row("palette-llm", vision=False)], 1) is None
    assert platform_models.pick(None, 0) == (None, {})  # no catalog: the platform chooses
    service = FakeLLM(rows)
    asyncio.run(llm.llm_json(_ctx(service), "sys", "describe", image_b64="QUJD", mime="image/png"))
    call = service.calls[0]
    assert call["model"] == "gpt-5.5"
    parts = call["messages"][0]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,QUJD")


def test_the_router_stream_is_joined_into_one_reply():
    lines = [
        'data: {"choices":[{"delta":{"content":"```json\\n{\\""},"index":0}]}',
        "",
        'data: {"choices":[{"delta":{"content":"ok\\": true}\\n```"},"index":0}]}',
        "data: [DONE]",
    ]
    assert llm.parse_json_block(llm.sse_text(lines)) == {"ok": True}
    with pytest.raises(RuntimeError):
        llm.sse_text(['data: {"error":{"message":"quota"}}'])


def test_a_chosen_router_runs_first_with_its_base_url(monkeypatch):
    calls = []
    _fake_provider(monkeypatch, "router", calls)
    out = asyncio.run(llm.llm_json(_ctx(None, LLM_ROUTER_API_KEY="r", LLM_ROUTER_BASE_URL="https://r.test"),
                                   "sys", "hi", "router", max_tokens=4000))
    assert out == {"from": "router"}
    assert calls == [("router", {"max_tokens": 4000, "base_url": "https://r.test"})]


def test_a_workspace_without_models_steps_aside_without_a_call(monkeypatch, caplog):
    service = FakeLLM([_row("palette-llm", ok=False, reason="billing_disabled"),
                       _row("claude-opus-5-5", ok=False, reason="org_policy")])
    calls = []
    _fake_provider(monkeypatch, "claude_opus", calls)
    out = asyncio.run(llm.llm_json(_ctx(service, ANTHROPIC_API_KEY="a"), "sys", "hi", "palette", max_tokens=6000))
    assert out == {"from": "claude_opus"} and not service.calls
    assert calls[0][1] == {"max_tokens": 6000}
    with caplog.at_level("WARNING"), pytest.raises(Exception) as err:
        asyncio.run(llm.llm_json(_ctx(service), "sys", "hi"))
    # Users get a plain message; the platform's reasons stay in the server log.
    assert "billing_disabled" not in str(err.value.detail) and "palette" not in str(err.value.detail)
    assert "billing_disabled" in caplog.text and "org_policy" in caplog.text
    assert not service.calls


def test_models_report_the_platform_and_its_state():
    service = FakeLLM([_row("palette-llm", ok=False, reason="billing_disabled")])
    ctx = _ctx(service, media=FakeMedia([platform_models.PLATFORM_TTS,
                                         "fal-ai/kling-video/v3/standard/text-to-video"]))
    asyncio.run(platform_models.warm(ctx))
    report = catalog.catalog(ctx)
    palette = report["text"][0]
    assert palette["id"] == "palette" and palette["available"] and palette["reason"] == "billing_disabled"
    assert report["default_text"] == "palette"
    video = {m["id"]: m["available"] for m in report["video"]}
    assert video["kling_3_0_standard"] and not video["seedance_2_0"]
    assert report["audio"] == {"tts": True, "music": False}


# --- images and video ---------------------------------------------------------------------------------------------------

def _no_router(monkeypatch):
    async def unavailable(*a, **k):
        raise generate_image.RouterUnavailable("off")

    monkeypatch.setattr(generate_image, "generate_image_router", unavailable)
    monkeypatch.setattr(generate_image, "edit_image_router", unavailable)


def test_images_use_the_platform_only_for_a_listed_endpoint(monkeypatch):
    _no_router(monkeypatch)
    media = FakeMedia(["fal-ai/flux/schnell"])
    ctx = _ctx(media=media)
    urls = asyncio.run(generate_image._generate_urls(ctx, "flux_schnell", "a cat", {"aspect_ratio": "16:9"}, [], 1))
    assert urls == ["https://fal.media/still.png"]
    assert media.calls[0][1] == "fal-ai/flux/schnell" and media.calls[0][2]["image_size"] == "landscape_16_9"
    # nano-banana-pro is not in the catalog: no platform call, the app's own providers (none here) are used
    media.calls.clear()
    with pytest.raises(RuntimeError):
        asyncio.run(generate_image._generate_urls(ctx, "nano_banana_pro", "a cat", {}, [], 1))
    assert not media.calls


def test_video_uses_the_platform_only_for_a_listed_endpoint():
    media = FakeMedia(["fal-ai/kling-video/v3/standard/text-to-video", "fal-ai/kling-video/v3/pro/image-to-video"])
    ctx = _ctx(media=media)
    spec = {"prompt": "waves", "start_image": None}
    url = asyncio.run(generate_video.platform_clip(ctx, "kling_3_0_standard", spec, {"duration": "5", "audio": False}))
    assert url == "https://fal.media/clip.mp4"
    _kind, model, payload = media.calls[0]
    assert model == "fal-ai/kling-video/v3/standard/text-to-video"
    assert payload["prompt"] == "waves" and payload["generate_audio"] is False and payload["duration"] == "5"
    url = asyncio.run(generate_video.platform_clip(ctx, "kling_3_0_pro", {**spec, "start_image": "https://x/a.png"}, {}))
    assert url and media.calls[1][1] == "fal-ai/kling-video/v3/pro/image-to-video"
    assert media.calls[1][2]["start_image_url"] == "https://x/a.png"
    assert asyncio.run(generate_video.platform_clip(ctx, "seedance_2_0", spec, {})) is None
    assert len(media.calls) == 2


def test_a_slow_platform_job_is_collected_not_paid_for_twice(monkeypatch):
    from palette_sdk.platform_services import MediaStillRunning

    class Slow(FakeMedia):
        polls = 0

        async def run(self, model, payload=None, timeout=None):
            self.calls.append(("run", model, payload))
            raise MediaStillRunning("req-1", model)

        async def result(self, handle, model=None):
            Slow.polls += 1
            if Slow.polls < 2:
                raise MediaStillRunning("req-1", model)
            return {"video": {"url": "https://fal.media/late.mp4"}}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(platform_models.asyncio, "sleep", no_sleep)
    media = Slow()
    out = asyncio.run(platform_models.platform_run(_ctx(media=media), "fal-ai/kling-video/v3/pro/text-to-video", {}, 10))
    assert out["video"]["url"].endswith("late.mp4") and Slow.polls == 2 and len(media.calls) == 1

    class Stub(FakeMedia):
        async def run(self, model, payload=None, timeout=None):
            return {"pltt_dev_stub": True}

    with pytest.raises(platform_models.PlatformUnavailable):
        asyncio.run(platform_models.platform_run(_ctx(media=Stub()), "fal-ai/flux/dev", {}, 10))


def test_a_keyless_dev_stub_is_not_a_platform():
    class Off(FakeMedia):
        live = False

    assert platform_models.media_service(_ctx(media=Off())) is None
    assert not asyncio.run(platform_models.media_available(_ctx(media=Off([platform_models.PLATFORM_TTS])),
                                                           platform_models.PLATFORM_TTS))


# --- voice --------------------------------------------------------------------------------------------------------------

class _Resp:
    content = b"ID3" + b"\x00" * 16000

    def raise_for_status(self):
        pass


class _Client:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _Resp()


def test_narration_uses_the_platform_voice_first(monkeypatch):
    eleven = []

    async def own_key(*a, **k):
        eleven.append(a)
        return b"own", []

    monkeypatch.setattr(elevenlabs_helper, "generate_narration", own_key)
    monkeypatch.setattr(elevenlabs_helper.httpx, "AsyncClient", _Client)

    async def seconds(_audio):
        return 2.0

    monkeypatch.setattr(elevenlabs_helper, "_audio_seconds", seconds)
    media = FakeMedia([platform_models.PLATFORM_TTS])
    ctx = _ctx(media=media)
    audio, words, provider = asyncio.run(elevenlabs_helper.narrate(ctx, "k", "Hello there friend", "voice123", "en"))
    assert provider.startswith("platform:") and audio.startswith(b"ID3") and not eleven
    _kind, model, kw = media.calls[0]
    assert model == platform_models.PLATFORM_TTS and kw["voice"] == "voice123" and kw["language_code"] == "en"
    assert [w["text"] for w in words] == ["Hello", "there", "friend"] and words[-1]["end"] == pytest.approx(2.0)
    # no voice chosen: the platform's default ElevenLabs voice
    asyncio.run(elevenlabs_helper.narrate(ctx, None, "Hi", None))
    assert media.calls[1][2]["voice"] == "Rachel"
    # not in the catalog: the app's own ElevenLabs key, without a platform call
    platform_models.clear_caches()
    other = FakeMedia([])
    _audio, _words, provider = asyncio.run(elevenlabs_helper.narrate(_ctx(media=other), "k", "Hi", "voice123"))
    assert provider.startswith("elevenlabs") and eleven and not other.calls


def test_platform_timestamps_become_word_cues():
    alignment = {"characters": list("Hi yo"), "character_start_times_seconds": [0, .1, .2, .3, .4],
                 "character_end_times_seconds": [.1, .2, .3, .4, .5]}
    assert [w["text"] for w in elevenlabs_helper._platform_words(alignment)] == ["Hi", "yo"]
    rows = [{"text": "Hi", "start": 0, "end": .2}, {"word": "yo", "start": .3, "end": .5}]
    assert elevenlabs_helper._platform_words(rows)[1] == {"text": "yo", "start": .3, "end": .5}
