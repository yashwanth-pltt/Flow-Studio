# Flow Video 2.0 (`pltt-video-studio-2`)

Version 2 of the Palette video studio. It uses the **Palette Video 2.0** UI (landing composer, Create / Library / Project / Archive, Storyboard · Sequence · Studio) on the **storyboard-video** backend, plus the multi-model per-scene generation from **creative-labs-videos**.

## What it does

- **Home.** Describe a video and pick a **text model**, an AUTO or MANUAL run mode, video settings (engine, ratio, resolution, target length) and image settings.
  - Text models come from `GET /models`: Palette (the platform `ctx.llm` service), Claude Opus / Sonnet / Haiku, GPT and Gemini.
  - Only models whose keys are configured are enabled.
- **AI pipeline.**
  - The chosen text model plans the brief into scenes: title, story, style bible, and per-scene image prompt, script, caption and motion.
  - Each scene then gets a still (Nano Banana / GPT Image / Midjourney / Flux, with Characters and Locations used as references) and a motion prompt.
  - Finally it is animated into a clip (Kling 3.0 Standard/Pro, Kling O3, Seedance 2.0), with Fal first and Runware as the fallback.
  - AUTO runs everything. MANUAL stops after planning.
- **Chat.** "Message Palette" sends instructions to the same text model. It can re-plan, edit scenes, generate, narrate or compose music.
- **Editing.** Library categories (Characters, Locations, …), AI category detection, Bin, uploads, screen/voice recording, canvas scale/position, trims, transitions, timed captions, voice/music tracks with a volume mix, and camera paths. "Generate camera move" animates the first frame along the drawn path.
- **Audio.** ElevenLabs voice-over (with word-timed captions) and music.
- **Export.** A server-side ffmpeg render to MP4, saved to Created videos, with captions burned in using the bundled Noto Sans KR. SRT and project JSON downloads are also available.
- **Language.** Follows the Palette OS language (English / Korean).

## Local development

```sh
npm install
cp .env.example .env            # fill in provider keys
npm run build:css               # after changing globals.css or Tailwind classes
pltt dev                        # app http://localhost:7321, backend :8732 (SQLite)
```

- **Postgres instead of SQLite:** run `docker compose up -d postgres` and set `PALETTE_DEV_DATABASE_URL` (port 5455).
- **Keys:** `FAL_KEY` is required for generation. `RUNWARE_KEY`, `MIDJOURNEY_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_KEY`, `GOOGLE_AI_API_KEY`, `ELEVEN_API_KEY` and `LLM_ROUTER_*` are optional.

## Checks

```sh
pltt build && pltt test
PYTHONPATH=node_modules/@palettelab/cli/backend-sdk:. .palette/test-venv/bin/python -m pytest backend/tests
```

## Publish

`npm run publish:staging` bumps the version in `palette-plugin.json` and `package.json`, runs `pltt test` and `pltt package`, then publishes.

## Layout

- `backend/api/main.py`: the router entry. It keeps the cross-plugin module-cache eviction.
- `backend/api/core/`:
  - Ported from storyboard: `fal_helper`, `runware_helper`, `router_helper`, `midjourney_helper`, `elevenlabs_helper`, `video_merge`, `storage_helper`, `org_db`, `bg_session`, `sse_manager`, `secrets`.
  - New: `llm`, `catalog`, `timeline` (export renderer), `camera_prompt`, `assets_service`, `media_probe`.
- `backend/api/routes/`: `general` (status/models/bootstrap/preferences), `assets`, `projects` (projects/versions/chat/export), `story` (plan, video-prompts), `generate_image`, `generate_video` (incl. retry and camera), `audio`, `generations`, `events`.
- `backend/migrations/versions/001_init.py`: `projects`, `assets`, `items`, `versions`, `messages`, `preferences`, all with org row-level security.
- `frontend/app/page.tsx`: the reference UI, wired to the backend through `hooks/use-workspace-sync.ts` (persistence), `hooks/use-studio-ai.ts` (pipeline) and `hooks/use-generation-events.ts` (SSE plus a poll fallback).
