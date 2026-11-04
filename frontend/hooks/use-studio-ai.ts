'use client';
import { useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { api, type GenerationItem } from '@/lib/api';
import { createScene, type Asset, type Project, type Scene } from '@/lib/palette/model';
import { generationSettings, type GenerationSettings } from '@/lib/palette/generation';
import { normalizeScene } from '@/lib/palette/studio';
import { userError } from '@/lib/palette/user-error';
import type { Translate } from '@/lib/i18n';

type Deps = {
  projectsRef: React.MutableRefObject<Project[]>;
  assetsRef: React.MutableRefObject<Asset[]>;
  setProjects: React.Dispatch<React.SetStateAction<Project[]>>;
  upsertAsset: (a: Asset) => void;
  flush: (projectId?: string) => Promise<void>;
  t: Translate;
  lang: string;
  /** Auto mode: export the finished film (the page's export, with its defaults). */
  onAutoExport?: (projectId: string) => void;
};

const REFERENCE_CATEGORIES = new Set(['Characters', 'Locations']);
const errorText = (e: unknown) => userError(e instanceof Error ? e.message : String(e));

/**
 * The AI pipeline behind the chat and composer (condensed port of the
 * storyboard app's story-os engine): plan a brief into scenes, create scene
 * stills, write motion prompts, animate clips, retry, voice-over and music.
 * Every job id is written into the project doc so a reload picks it up
 * again through useGenerationEvents' poll.
 */
export function useStudioAI({ projectsRef, assetsRef, setProjects, upsertAsset, flush, t, lang, onAutoExport }: Deps) {
  const running = useRef(new Set<string>());

  const updateProject = useCallback((projectId: string, fn: (p: Project) => Project) => {
    setProjects(ps => ps.map(p => (p.id === projectId ? { ...fn(p), updated: Date.now() } : p)));
  }, [setProjects]);

  const patchScene = useCallback((projectId: string, sceneId: string, patch: Partial<Scene> | ((s: Scene) => Partial<Scene>)) => {
    updateProject(projectId, p => ({
      ...p,
      scenes: p.scenes.map(s => (s.id === sceneId ? { ...s, ...(typeof patch === 'function' ? patch(s) : patch) } : s)),
    }));
  }, [updateProject]);

  const agentSay = useCallback((projectId: string, text: string) => {
    updateProject(projectId, p => ({ ...p, messages: [...p.messages, { id: crypto.randomUUID(), role: 'agent', text }] }));
  }, [updateProject]);

  const project = (id: string) => projectsRef.current.find(p => p.id === id);
  const asset = (id?: string) => (id ? assetsRef.current.find(a => a.id === id) : undefined);

  /** Characters / Locations images of the project (plus scene-specific refs) used as visual references. */
  const referenceIds = (p: Project, scene?: Scene) => {
    const trashed = (a: Asset) => a.trashed;
    const ids = new Set(scene?.referenceAssetIds || []);
    for (const a of assetsRef.current) {
      if (a.type !== 'image' || trashed(a) || !REFERENCE_CATEGORIES.has(a.category)) continue;
      if (a.projectId === p.id || (p.assetIds || []).includes(a.id)) ids.add(a.id);
    }
    return [...ids].slice(0, 7);
  };

  // ---- Planning -----------------------------------------------------------

  const startPlan = useCallback(async (projectId: string, brief: string, opts: { mode?: 'auto' | 'manual'; attachmentId?: string } = {}) => {
    const p = project(projectId);
    if (!p) return;
    const g = generationSettings(p.generation);
    const mode = opts.mode || g.mode;
    // Analyse reference images first so the planner can cast them into scenes (their frames are generated from
    // these references and the planned scenes — never uploaded).
    const refIds = [...new Set([...(opts.attachmentId ? [opts.attachmentId] : []), ...referenceIds(p)])].slice(0, 5);
    const references: { index: number; asset_id: string; analysis?: any }[] = [];
    for (const [i, id] of refIds.entries()) {
      const a = asset(id);
      if (!a || a.type === 'audio') continue;
      let analysis = a.analysis;
      if (!analysis?.description) {
        try { analysis = (await api.analyzeAsset(id, { llm: g.model, language: lang })).analysis; } catch { analysis = null; }
      }
      if (analysis) references.push({ index: i + 1, asset_id: id, analysis });
    }
    try {
      await flush(projectId);
      const { generation_id } = await api.plan({
        // The user's length setting decides the total; the planner fits the scene count to it.
        prompt: brief, total_duration: g.duration, aspect_ratio: g.ratio,
        video_model: g.engine, scene_count: null, references, llm: g.model,
        first_frame: null, last_frame: null, flow: g.flow,
        language: lang, project_id: projectId,
      });
      updateProject(projectId, pr => ({ ...pr, jobs: { ...pr.jobs, plan: { generationId: generation_id, status: 'pending', mode, brief } } }));
    } catch (e) {
      agentSay(projectId, `⚠️ ${errorText(e)}`);
      toast.error(t(errorText(e)));
    }
  }, [flush, lang, updateProject, agentSay]);

  // ---- Scene generation -------------------------------------------------------


  const startSceneImage = useCallback(async (projectId: string, sceneId: string, next?: 'video' | 'flow') => {
    const p = project(projectId);
    const s = p?.scenes.find(x => x.id === sceneId);
    if (!p || !s) return;
    const g = generationSettings(p.generation);
    const prompt = (s.prompt || [s.name, s.script].filter(Boolean).join('. ')).trim();
    if (!prompt) { toast.error(t('Describe the scene first.')); return; }
    try {
      const { generation_id } = await api.generateImage({
        prompt, model: g.image.engine, aspect_ratio: g.ratio, resolution: g.image.resolution,
        quality: g.image.quality, count: 1, references: referenceIds(p, s), project_id: projectId,
        scene_id: sceneId, source: 'story_scene', name: s.name,
      });
      patchScene(projectId, sceneId, { gen: { stage: 'image', status: 'pending', generationId: generation_id, next } });
    } catch (e) {
      patchScene(projectId, sceneId, { gen: { stage: 'image', status: 'failed', generationId: '', error: errorText(e) } });
      toast.error(t(errorText(e)));
    }
  }, [patchScene, t]);

  const startSceneVideo = useCallback(async (projectId: string, sceneId: string, startAssetId?: string, endAssetId?: string) => {
    const p = project(projectId);
    const s = p?.scenes.find(x => x.id === sceneId);
    if (!p || !s) return;
    const g = generationSettings(p.generation);
    // The clip's first frame: given, else (Flow) the previous scene's clip's LAST frame ("<video id>#last", taken
    // from the finished clip by the server) so the film is one continuous shot, else the scene's own still.
    const index = p.scenes.findIndex(x => x.id === sceneId);
    const prev = index > 0 ? p.scenes[index - 1] : undefined;
    const flowStart = g.flow && prev && asset(prev.assetId)?.type === 'video' ? `${prev.assetId}#last` : undefined;
    const start = startAssetId || flowStart || s.assetId;
    if (!start) { await startSceneImage(projectId, sceneId, 'video'); return; }
    const end = endAssetId && endAssetId !== start && asset(endAssetId)?.type !== 'audio' ? endAssetId : undefined;
    const engine = s.engine || g.engine;
    let prompt = s.videoPrompt;
    if (!prompt) {
      try {
        const res = await api.videoPrompts({
          story: p.story, bible: p.bible, video_model: engine, audio: g.audio, llm: g.model,
          scenes: [{ index: 0, title: s.name, duration: s.duration, image_prompt: s.prompt || s.script || s.name }],
        });
        prompt = res.scenes[0]?.video_prompt;
        if (prompt) patchScene(projectId, sceneId, { videoPrompt: prompt });
      } catch { /* fall back below */ }
    }
    prompt = prompt || `${s.prompt || s.script || s.name} — cinematic camera movement, natural motion`;
    if (start.endsWith('#last')) prompt = `${prompt} The shot continues seamlessly from the provided first frame (the previous shot's last frame), same action and camera, no cut.`;
    if (end) prompt = `${prompt} The shot ends exactly on the provided final frame, continuing the same action without a cut.`;
    try {
      const { generation_id } = await api.generateVideo({
        prompt, model: engine, duration: s.duration, aspect_ratio: g.ratio, resolution: g.resolution,
        audio: g.audio, start_image: start, end_image: end, project_id: projectId, scene_id: sceneId, name: s.name,
      });
      patchScene(projectId, sceneId, { gen: { stage: 'video', status: 'pending', generationId: generation_id } });
    } catch (e) {
      patchScene(projectId, sceneId, { gen: { stage: 'video', status: 'failed', generationId: '', error: errorText(e) } });
      toast.error(t(errorText(e)));
    }
  }, [patchScene, startSceneImage]);

  /** Auto mode: once nothing is pending, export the film (once). */
  const maybeAutoExport = useCallback((projectId: string) => {
    const p = project(projectId);
    if (!p?.jobs?.auto?.export || p.jobs.flow || p.scenes.some(s => s.gen?.status === 'pending')) return;
    updateProject(projectId, pr => ({ ...pr, jobs: { ...pr.jobs, auto: undefined } }));
    if (p.scenes.some(s => s.gen?.status === 'failed')) toast(t('Some scenes couldn’t be animated; exporting the film with what is ready.'));
    agentSay(projectId, t('Every scene is ready — exporting the film now…'));
    onAutoExport?.(projectId);
  }, [updateProject, agentSay, onAutoExport, t]);

  /** Starts the Flow chain's clip at position `index` (its start: the previous clip's last frame, or `start`). */
  const startChainClip = useCallback(async (projectId: string, index: number, start?: string, retried = false) => {
    const p = project(projectId);
    const job = p?.jobs?.flow;
    if (!p || !job) return;
    const id = job.sceneIds[index];
    if (!id) {  // the chain is done
      updateProject(projectId, pr => ({ ...pr, jobs: { ...pr.jobs, flow: undefined } }));
      setTimeout(() => maybeAutoExport(projectId), 50);
      return;
    }
    updateProject(projectId, pr => ({ ...pr, jobs: { ...pr.jobs, flow: { ...job, phase: 'clips', index, start, retried } } }));
    await startSceneVideo(projectId, id, start);
  }, [updateProject, startSceneVideo, maybeAutoExport]);

  /** Flow phase 2: once every still of the batch is done (or failed), the clips run ONE AFTER ANOTHER — the first
   * starts on its scene's still, each next one on the previous clip's real last frame (one continuous film). */
  const startFlowClips = useCallback(async (projectId: string) => {
    const p = project(projectId);
    const job = p?.jobs?.flow;
    if (!p || !job || job.phase === 'clips') return;
    const scenes = p.scenes.filter(s => job.sceneIds.includes(s.id));
    if (scenes.some(s => s.gen?.status === 'pending' && s.gen.stage === 'image')) return;
    // only scenes that can be animated, in story order
    const order = p.scenes.filter(s => job.sceneIds.includes(s.id) && asset(s.assetId)?.type === 'image').map(s => s.id);
    updateProject(projectId, pr => ({ ...pr, jobs: { ...pr.jobs, flow: { sceneIds: order, phase: 'clips', index: 0 } } }));
    setTimeout(() => { void startChainClip(projectId, 0); }, 50);
  }, [updateProject, startChainClip]);

  const generateScenes = useCallback(async (projectId: string, sceneIds: string[], stage: 'image' | 'video' | 'both',
    opts: { autoExport?: boolean } = {}) => {
    // Busy until every job below has started (saving and prompt writing take a few seconds).
    updateProject(projectId, pr => ({ ...pr, jobs: { ...pr.jobs, preparing: true, ...(opts.autoExport ? { auto: { export: true } } : {}) } }));
    try {
      await runGeneration(projectId, sceneIds, stage);
    } finally {
      updateProject(projectId, pr => ({ ...pr, jobs: { ...pr.jobs, preparing: undefined } }));
      // An AUTO run whose jobs all ended already (nothing to make, or every start failed) finishes here.
      setTimeout(() => maybeAutoExport(projectId), 50);
    }
  }, [updateProject, maybeAutoExport]); // eslint-disable-line react-hooks/exhaustive-deps -- runGeneration reads refs only

  const runGeneration = async (projectId: string, sceneIds: string[], stage: 'image' | 'video' | 'both') => {
    await flush(projectId);
    const p = project(projectId);
    if (!p) return;
    const targets = (sceneIds.length ? p.scenes.filter(s => sceneIds.includes(s.id)) : p.scenes.filter(s => !s.assetId || asset(s.assetId)?.type === 'image'))
      .filter(s => s.gen?.status !== 'pending');
    if (!targets.length) { toast(t('Every scene already has media.')); return; }
    // One LLM call writes motion prompts for every scene that will be animated.
    if (stage !== 'image') {
      const need = targets.filter(s => !s.videoPrompt && (s.prompt || s.script));
      if (need.length > 1) {
        const g = generationSettings(p.generation);
        try {
          const res = await api.videoPrompts({
            story: p.story, bible: p.bible, video_model: g.engine, audio: g.audio, llm: g.model,
            scenes: need.map((s, i) => ({ index: i, title: s.name, duration: s.duration, image_prompt: s.prompt || s.script })),
          });
          for (const r of res.scenes) {
            const s = need[r.index];
            if (s && r.video_prompt) { s.videoPrompt = r.video_prompt; patchScene(projectId, s.id, { videoPrompt: r.video_prompt }); }
          }
        } catch { /* per-scene fallback */ }
      }
    }
    // Flow: make every missing still first, then start all clips at once, so each
    // clip can end on the next scene's still (see startFlowClips).
    if (stage === 'both' && generationSettings(p.generation).flow && targets.length > 1) {
      const missing = targets.filter(s => !asset(s.assetId));
      updateProject(projectId, pr => ({ ...pr, jobs: { ...pr.jobs, flow: { sceneIds: targets.map(s => s.id), phase: 'stills' } } }));
      if (!missing.length) { setTimeout(() => { void startFlowClips(projectId); }, 50); return; }
      for (const s of missing) await startSceneImage(projectId, s.id, 'flow');
      return;
    }
    for (const s of targets) {
      const a = asset(s.assetId);
      if (stage === 'image' || (!a && stage === 'both')) await startSceneImage(projectId, s.id, stage === 'both' ? 'video' : undefined);
      else if (a?.type === 'image' || stage === 'video') await startSceneVideo(projectId, s.id);
    }
  };

  const retryScene = useCallback(async (projectId: string, sceneId: string) => {
    const s = project(projectId)?.scenes.find(x => x.id === sceneId);
    if (!s) return;
    if (s.gen?.stage === 'video' && s.gen.generationId) {
      try {
        const { generation_id } = await api.retryVideo({ generation_id: s.gen.generationId });
        patchScene(projectId, sceneId, { gen: { stage: 'video', status: 'pending', generationId: generation_id } });
        return;
      } catch { /* fall through to a fresh run */ }
    }
    if (s.gen?.stage === 'video') await startSceneVideo(projectId, sceneId);
    else await startSceneImage(projectId, sceneId, s.gen?.next);
  }, [patchScene, startSceneImage, startSceneVideo]);

  const generateCamera = useCallback(async (projectId: string, sceneId: string, motion: NonNullable<Scene['cameraMotion']>) => {
    const p = project(projectId);
    if (!p) return;
    const g = generationSettings(p.generation);
    try {
      const { generation_id } = await api.generateCamera({
        project_id: projectId, scene_id: sceneId, asset_id: motion.assetId, preset: motion.preset,
        keyframes: motion.keyframes, duration: motion.duration, resolution: motion.resolution,
        aspect_ratio: g.ratio, instructions: motion.instructions, quality_retry: motion.qualityRetry,
      });
      patchScene(projectId, sceneId, { gen: { stage: 'video', status: 'pending', generationId: generation_id } });
      toast.success(t('Generating the camera move…'));
    } catch (e) {
      toast.error(t(errorText(e)));
    }
  }, [patchScene, t]);

  // ---- Audio ----------------------------------------------------------------

  const voiceover = useCallback(async (projectId: string, script: string, voiceId?: string) => {
    try {
      const res = await api.tts({ text: script, voice_id: voiceId, language: lang, project_id: projectId });
      upsertAsset(res.asset);
      updateProject(projectId, p => ({
        ...p, voiceId: res.asset.id, assetIds: [...new Set([...(p.assetIds || []), res.asset.id])],
        captions: res.captions.length && !(p.captions || []).length
          ? res.captions.map(c => ({ id: crypto.randomUUID(), text: c.text, start: c.start, end: c.end }))
          : p.captions,
      }));
      toast.success(t('Voice-over added to the voice track.'));
      return res.asset;
    } catch (e) {
      toast.error(t(errorText(e)));
    }
  }, [lang, updateProject, upsertAsset, t]);

  const music = useCallback(async (projectId: string, prompt: string) => {
    const p = project(projectId);
    const total = p?.scenes.reduce((n, s) => n + s.duration, 0) || 30;
    try {
      const { generation_id } = await api.music({ prompt, duration_seconds: Math.max(10, Math.min(120, total)), project_id: projectId });
      updateProject(projectId, pr => ({ ...pr, jobs: { ...pr.jobs, music: { generationId: generation_id, status: 'pending' } } }));
      toast(t('Composing music…'));
    } catch (e) {
      toast.error(t(errorText(e)));
    }
  }, [updateProject, t]);

  // ---- Results ----------------------------------------------------------------

  const handleItem = useCallback((item: GenerationItem) => {
    const gid = item.generation_id;
    if (!gid) return;
    const ok = item.status === 'completed';
    const err = item.error || item.error_message || t('Generation failed.');

    // Story plan → scenes.
    const planOwner = projectsRef.current.find(p => p.jobs?.plan?.generationId === gid);
    if (planOwner && (item.type === 'story_plan' || !item.type)) {
      const job = planOwner.jobs!.plan!;
      if (!ok) {
        updateProject(planOwner.id, p => ({ ...p, jobs: { ...p.jobs, plan: { ...job, status: 'failed', error: err } } }));
        agentSay(planOwner.id, `⚠️ ${err}`);
        return;
      }
      const gp = item.gen_params || {};
      const flow = generationSettings(planOwner.generation).flow;
      let scenes: Scene[] = (gp.scenes || []).map((s: any, i: number) => ({
        ...createScene('', s.title || t('Scene {n}', { n: String(i + 1).padStart(2, '0') })),
        duration: Number(s.duration) || 5, script: s.script || '', caption: s.caption || '',
        motion: ['Still', 'Push in', 'Pan left'].includes(s.motion) ? s.motion : 'Still',
        prompt: s.image_prompt || '', referenceAssetIds: s.reference_asset_ids || [], engine: gp.video_model,
      }));
      // Every frame is generated (scene stills from the references and the plan; in Flow each clip continues from
      // the previous clip's last frame). Flow keeps every scene a plain cut with no edit motion, so they join seamlessly.
      scenes = scenes.map(s => ({ ...s, ...(flow ? { motion: 'Still' as const, transition: 'Cut' as const } : {}) }));
      updateProject(planOwner.id, p => ({
        ...p,
        name: gp.title && (p.name === t('Untitled project') || p.name.endsWith('…') || p.name === job.brief) ? gp.title : p.name,
        story: gp.story || p.story, bible: gp.bible || p.bible,
        scenes: p.scenes.length && p.scenes.every(s => !s.assetId) ? scenes : [...p.scenes, ...scenes],
        // AUTO runs straight on: the project is busy from the same update that adds the scenes.
        jobs: { ...p.jobs, plan: undefined, ...(job.mode === 'auto' ? { auto: { export: true }, preparing: true } : {}) },
      }));
      const sceneList = scenes.map((s, i) => {
        const detail = (s.script || s.caption).trim();
        return `${i + 1}. ${s.name} · ${s.duration}s${detail ? ` — ${detail}` : ''}`;
      }).join('\n');
      agentSay(planOwner.id, [
        t('Planned {n} scenes:', { n: scenes.length }),
        sceneList,
        job.mode === 'auto'
          ? t('Creating stills and clips now…')
          : t('Review them on the Storyboard, then press Generate to create the stills and clips.'),
      ].filter(Boolean).join('\n\n'));
      if (job.mode === 'auto' && !running.current.has(gid)) {
        running.current.add(gid);
        setTimeout(() => { void generateScenes(planOwner.id, scenes.map(s => s.id), 'both', { autoExport: true }); }, 50);
      } else if (job.mode !== 'auto') {
        // Manual mode stops after planning; offer the next step right away.
        toast(t('{n} scenes are ready to generate.', { n: scenes.length }), {
          duration: 15000, action: { label: t('Generate'), onClick: () => void generateScenes(planOwner.id, scenes.map(s => s.id), 'both') },
        });
      }
      return;
    }

    // Music.
    const musicOwner = projectsRef.current.find(p => p.jobs?.music?.generationId === gid);
    if (musicOwner) {
      if (ok && item.asset) {
        upsertAsset(item.asset);
        updateProject(musicOwner.id, p => ({ ...p, musicId: item.asset!.id, assetIds: [...new Set([...(p.assetIds || []), item.asset!.id])], jobs: { ...p.jobs, music: undefined } }));
        toast.success(t('Music added to the music track.'));
      } else if (!ok) {
        updateProject(musicOwner.id, p => ({ ...p, jobs: { ...p.jobs, music: { generationId: gid, status: 'failed', error: err } } }));
        toast.error(t(err));
      }
      return;
    }

    // Scene still / clip / camera move.
    const owner = projectsRef.current.find(p => p.scenes.some(s => s.gen?.generationId === gid));
    const scene = owner?.scenes.find(s => s.gen?.generationId === gid);
    if (!owner || !scene?.gen) {
      if (ok && item.asset) upsertAsset(item.asset);
      return;
    }
    const chain = owner.jobs?.flow?.phase === 'clips' && scene.gen.stage === 'video'
      && owner.jobs.flow.sceneIds[owner.jobs.flow.index ?? -1] === scene.id ? owner.jobs.flow : undefined;
    if (!ok || !item.asset) {
      patchScene(owner.id, scene.id, { gen: { ...scene.gen, status: 'failed', error: err } });
      // A failed still must not hold up the rest of its Flow batch.
      if (scene.gen.next === 'flow') setTimeout(() => { void startFlowClips(owner.id); }, 50);
      if (chain) {
        const i = chain.index ?? 0;
        if (!chain.retried) setTimeout(() => { void startChainClip(owner.id, i, chain.start, true); }, 50);  // once more
        else {
          // the chain moves on: the next clip starts from its own still
          toast(t('The flow broke at “{name}”; the next scene starts from its own frame.', { name: scene.name }));
          setTimeout(() => { void startChainClip(owner.id, i + 1); }, 50);
        }
      } else setTimeout(() => maybeAutoExport(owner.id), 50);
      return;
    }
    const a = item.asset;
    upsertAsset(a);
    const next = scene.gen.next;
    updateProject(owner.id, p => ({
      ...p,
      assetIds: [...new Set([...(p.assetIds || []), a.id])],
      scenes: p.scenes.map(s => (s.id === scene.id ? { ...normalizeScene(s, a, { assetId: a.id, sourceIn: 0 }), gen: undefined } : s)),
    }));
    if (next === 'video') setTimeout(() => { void startSceneVideo(owner.id, scene.id, a.id); }, 50);
    else if (next === 'flow') setTimeout(() => { void startFlowClips(owner.id); }, 50);
    else if (chain && a.type === 'video') {
      // the next clip of the film starts on this clip's real last frame
      setTimeout(() => { void startChainClip(owner.id, (chain.index ?? 0) + 1, `${a.id}#last`); }, 50);
    } else if (scene.gen.stage === 'video') setTimeout(() => maybeAutoExport(owner.id), 50);
  }, [agentSay, generateScenes, patchScene, projectsRef, startSceneVideo, startFlowClips, startChainClip, maybeAutoExport, t, updateProject, upsertAsset]);

  /** After a reload: start Flow batches whose stills all finished while the app was closed. */
  const resumeFlow = useCallback((projectIds: string[]) => {
    for (const id of projectIds) {
      const jobs = project(id)?.jobs;
      if (jobs?.preparing) {
        // interrupted before its jobs started: nothing is running, so don't stay busy (or auto-export a half film)
        updateProject(id, pr => ({ ...pr, jobs: { ...pr.jobs, preparing: undefined, auto: undefined } }));
        continue;
      }
      if (jobs?.auto?.export && !jobs.flow) { setTimeout(() => maybeAutoExport(id), 50); continue; }
      const job = jobs?.flow;
      if (!job) continue;
      if (job.phase !== 'clips') { void startFlowClips(id); continue; }
      // a chain whose current clip finished (or was never started) while the app was closed
      const p = project(id)!;
      const current = p.scenes.find(s => s.id === job.sceneIds[job.index ?? 0]);
      if (current && current.gen?.status !== 'pending') {
        const a = asset(current.assetId);
        void startChainClip(id, (job.index ?? 0) + (a?.type === 'video' ? 1 : 0), a?.type === 'video' ? `${a.id}#last` : job.start);
      }
    }
  }, [startFlowClips, startChainClip, updateProject, maybeAutoExport]);

  return { startPlan, generateScenes, retryScene, generateCamera, voiceover, music, handleItem, resumeFlow };
}

/** Generation ids the UI is still waiting on (polled by useGenerationEvents). */
export function pendingGenerationIds(projects: Project[]): string[] {
  const ids: string[] = [];
  for (const p of projects) {
    if (p.jobs?.plan?.status === 'pending') ids.push(p.jobs.plan.generationId);
    if (p.jobs?.music?.status === 'pending') ids.push(p.jobs.music.generationId);
    for (const s of p.scenes) if (s.gen?.status === 'pending' && s.gen.generationId) ids.push(s.gen.generationId);
  }
  return ids;
}

export type { GenerationSettings };
