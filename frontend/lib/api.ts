// Typed wrappers for the Flow Video 2.0 backend (backend/api/routes/*).
import { apiGet, apiSend, apiUpload, discoverApiOrigin, mediaUrl } from './api-helper';
import type { Asset, Project, Version } from './palette/model';
import type { GenerationSettings } from './palette/generation';

export type TextModel = { id: string; label: string; provider: string; available: boolean; requires?: string };
export type VideoModel = { id: string; label: string; detail?: string; durations: number[]; resolutions: string[]; aspect_ratios: string[]; audio: boolean; /** Sound can't be turned off for this model. */ audio_fixed?: boolean; end_frame: boolean; reference_images: boolean; available: boolean };
export type ImageModel = { id: string; label: string; available: boolean; requires?: string };
export type Catalog = {
  text: TextModel[]; default_text: string;
  video: VideoModel[]; default_video: string;
  image: ImageModel[];
  audio: { tts: boolean; music: boolean };
  ffmpeg: boolean;
  providers: Record<string, boolean>;
};
export type ServerProject = { id: string; name: string; doc: Partial<Project>; rev: number; updated: number; thumbnail_url?: string | null };
export type Bootstrap = { user_id: string; projects: ServerProject[]; assets: Asset[]; versions: Version[]; preferences: Record<string, any>; capabilities: Catalog };
export type GenerationItem = {
  id: string; generation_id?: string; type?: string; status: string; url?: string; project_id?: string | null;
  scene_id?: string | null; model_name?: string; error?: string; error_message?: string; logs?: string[];
  gen_params?: Record<string, any>; asset?: Asset;
};
export type ChatAction =
  | { type: 'plan'; brief: string; total_duration?: number | null; scene_count?: number | null }
  | { type: 'update_scene'; scene_id: string; patch: Record<string, any> }
  | { type: 'generate'; scene_ids: string[]; stage: 'image' | 'video' | 'both' }
  | { type: 'voiceover'; script: string }
  | { type: 'music'; prompt: string };

/** Asset URLs from the backend may be plugin-relative (dev storage); make them loadable. */
export function browserAsset(a: Asset): Asset {
  return { ...a, src: mediaUrl(a.src), poster: a.poster ? mediaUrl(a.poster) : undefined };
}

export function toProject(p: ServerProject): Project {
  const doc = p.doc || {};
  return { scenes: [], assetIds: [], assetOrder: [], messages: [], ...doc, id: p.id, name: p.name, updated: p.updated, rev: p.rev } as Project;
}

export const api = {
  async bootstrap(): Promise<Bootstrap> {
    await discoverApiOrigin();
    const data = await apiGet<Bootstrap>('/bootstrap');
    return { ...data, assets: data.assets.map(browserAsset) };
  },
  models: () => apiGet<Catalog>('/models'),
  saveProject: (p: Project) => {
    const { id, name, updated, rev, ...doc } = p;
    void updated;
    return apiSend<{ id: string; rev: number; updated: number }>(`/projects/${id}`, 'PUT', { name, doc, base_rev: rev ?? null });
  },
  deleteProject: (id: string) => apiSend(`/projects/${id}`, 'DELETE'),
  savePreferences: (data: Record<string, any>) => apiSend('/preferences', 'PUT', { data }),
  createVersion: (v: Version) => apiSend('/versions', 'POST', { id: v.id, project_id: v.project.id, name: v.name, snapshot: v.project }),
  deleteVersion: (id: string) => apiSend(`/versions/${id}`, 'DELETE'),
  async upload(file: File, fields: { projectId?: string; category?: string; source?: 'upload' | 'recording' } = {}): Promise<Asset> {
    const f: Record<string, string> = { name: file.name, source: fields.source || 'upload' };
    if (fields.projectId) f.project_id = fields.projectId;
    if (fields.category) f.category = fields.category;
    const res = await apiUpload<{ asset: Asset }>('/assets/upload', file, f);
    return browserAsset(res.asset);
  },
  async patchAsset(id: string, patch: { name?: string; category?: string; trashed?: boolean; archived?: boolean; project_id?: string }): Promise<Asset> {
    return browserAsset((await apiSend<{ asset: Asset }>(`/assets/${id}`, 'PATCH', patch)).asset);
  },
  deleteAsset: (id: string) => apiSend(`/assets/${id}`, 'DELETE'),
  async bulkAssets(ids: string[], patch: { category?: string; trashed?: boolean; archived?: boolean }): Promise<Asset[]> {
    return (await apiSend<{ assets: Asset[] }>('/assets/bulk', 'POST', { ids, patch })).assets.map(browserAsset);
  },
  analyzeAsset: (id: string, body: { llm?: string; language?: string; apply_category?: boolean }) =>
    apiSend<{ analysis: { kind: string; name: string; description: string }; suggested_category?: string | null; asset?: Asset | null }>(`/assets/${id}/analyze`, 'POST', body),
  generation: (id: string) => apiGet<{ status: string; items: GenerationItem[]; assets: Asset[] }>(`/generations/${id}`),
  plan: (body: { prompt: string; total_duration: number; aspect_ratio: string; video_model: string; scene_count?: number | null; references?: { index: number; asset_id: string; analysis?: any }[]; first_frame?: number | null; last_frame?: number | null; flow?: boolean; llm?: string; language?: string; project_id?: string }) =>
    apiSend<{ generation_id: string }>('/story/plan', 'POST', body),
  videoPrompts: (body: { story?: string; prompt?: string; bible?: Record<string, string>; video_model: string; audio: boolean; scenes: { index: number; title: string; duration: number; image_prompt: string; camera?: string }[]; llm?: string }) =>
    apiSend<{ scenes: { index: number; video_prompt: string }[] }>('/story/video-prompts', 'POST', body),
  generateImage: (body: { prompt: string; model: string; aspect_ratio: string; resolution: string; quality: string; count: number; references?: string[]; project_id?: string; scene_id?: string; source?: string; name?: string }) =>
    apiSend<{ generation_id: string }>('/generate/image', 'POST', body),
  generateVideo: (body: { prompt: string; model: string; duration: number; aspect_ratio: string; resolution?: string; audio?: boolean; start_image?: string; end_image?: string; image_references?: string[]; project_id?: string; scene_id?: string; name?: string; source?: string }) =>
    apiSend<{ generation_id: string }>('/generate/video', 'POST', body),
  retryVideo: (body: { generation_id?: string; item_id?: string; model?: string }) => apiSend<{ generation_id: string }>('/generate/video/retry', 'POST', body),
  generateCamera: (body: { project_id?: string; scene_id?: string; asset_id: string; preset: string; keyframes: any[]; duration: number; resolution: string; aspect_ratio: string; instructions: string; quality_retry: boolean }) =>
    apiSend<{ generation_id: string }>('/generate/camera', 'POST', body),
  chat: (projectId: string, body: { text: string; model: string; mode: string; language: string; selected_scene_id?: string; generation?: GenerationSettings; message_id?: string }) =>
    apiSend<{ user_message: any; agent_message: { id: string; text: string }; actions: ChatAction[]; error?: string }>(`/projects/${projectId}/chat`, 'POST', body),
  exportProject: (projectId: string, body: { ratio: string; resolution: string; bitrate: string; burn_captions: boolean; name?: string }) =>
    apiSend<{ generation_id: string }>(`/projects/${projectId}/export`, 'POST', body),
  voices: () => apiGet<{ available: boolean; voices: { id: string; name: string; preview_url?: string }[] }>('/audio/voices'),
  async tts(body: { text: string; voice_id?: string; language?: string; project_id?: string; scene_id?: string; name?: string }) {
    const res = await apiSend<{ asset: Asset; captions: { text: string; start: number; end: number }[] }>('/audio/tts', 'POST', body);
    return { ...res, asset: browserAsset(res.asset) };
  },
  music: (body: { prompt: string; duration_seconds: number; project_id?: string }) => apiSend<{ generation_id: string }>('/audio/music', 'POST', body),
};
