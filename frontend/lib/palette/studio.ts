import type { Asset, Project, Scene } from './model';

/** Ids a project actually uses: its media list, scene clips/stills, voice and music. */
export function projectAssetIds(project: Project) {
  return new Set([...(project.assetIds || []), ...project.scenes.map(scene => scene.assetId), project.musicId, project.voiceId].filter((id): id is string => !!id));
}

export function projectMedia(project: Project, assets: Asset[]) {
  const ids = projectAssetIds(project);
  const media = assets.filter(asset => ids.has(asset.id)||asset.createdProjectId===project.id||asset.projectId===project.id);
  const order = new Map((project.assetOrder || []).map((id, index) => [id, index]));
  return media.sort((a, b) => (order.get(a.id) ?? Infinity) - (order.get(b.id) ?? Infinity));
}

/** Category rule shared by the Library and the project media panel. Images,
 * Videos and Voices & audio go by file type (generated stills are filed under
 * Scenes, references under Characters…); the rest by the asset's category. */
export function matchesCategory(asset: Asset, category: string) {
  switch (category) {
    case 'All media': case 'Bin': return true;
    case 'Images': return asset.type === 'image';
    case 'Videos': return asset.type === 'video';
    case 'Voices & audio': return asset.type === 'audio';
    case 'Uploads': return !!asset.uploaded;
    default: return asset.category === category;
  }
}

export function filterStudioMedia(assets: Asset[], category: string, query: string, trashed: string[], project?: Project) {
  const term = query.trim().toLocaleLowerCase();
  const used = project ? projectAssetIds(project) : new Set<string>();
  return assets.filter(asset => {
    // Exports live under Exported videos, unless this project uses one as media.
    if (asset.createdProjectId && category !== 'Bin' && !used.has(asset.id)) return false;
    // Archived media is only shown in the Archive.
    if (asset.archivedAt && category !== 'Bin') return false;
    const visible = category === 'Bin' ? trashed.includes(asset.id) : !trashed.includes(asset.id);
    return visible && matchesCategory(asset, category) && asset.name.toLocaleLowerCase().includes(term);
  });
}

export function reorderScenes(scenes: Scene[], id: string, target: number) {
  const from = scenes.findIndex(scene => scene.id === id);
  if (from < 0 || target < 0 || target >= scenes.length || from === target) return scenes;
  const next = [...scenes];
  const [scene] = next.splice(from, 1);
  next.splice(target, 0, scene);
  return next;
}

export function trimScene(scene: Scene, asset: Asset | undefined, from?: number, to?: number): Partial<Scene> {
  const max = asset?.type === 'video' && asset.duration ? asset.duration : 30;
  const currentStart = scene.sourceIn || 0;
  const currentEnd = Math.min(max, currentStart + scene.duration);
  const start = from === undefined || !Number.isFinite(from) ? currentStart : Math.max(0, Math.min(currentEnd - .1, from));
  const end = to === undefined || !Number.isFinite(to) ? currentEnd : Math.max(start + .1, Math.min(max, to));
  return { sourceIn: Math.round(start * 10) / 10, duration: Math.max(.1, Math.round((end - start) * 10) / 10) };
}

export function normalizeScene(scene:Scene, asset:Asset|undefined, patch:Partial<Scene>):Scene {
 const next={...scene,...patch};
 const max=asset?.type==='video'&&asset.duration?asset.duration:30;
 next.sourceIn=Math.max(0,Math.min(max-.1,Number.isFinite(next.sourceIn)?next.sourceIn||0:0));
 next.duration=Math.max(.1,Math.min(max-next.sourceIn,Number.isFinite(next.duration)?next.duration:scene.duration));
 return next;
}

// Move one asset without changing membership or the relative order of the others.
export function reorderMedia(ids: string[], id: string, targetId: string) {
  const from = ids.indexOf(id), to = ids.indexOf(targetId);
  if (from < 0 || to < 0 || from === to) return ids;
  const next = [...ids];
  next.splice(from, 1);
  next.splice(to, 0, id);
  return next;
}
