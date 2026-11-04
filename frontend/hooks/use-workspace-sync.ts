'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { Asset, Project, Version } from '@/lib/palette/model';
import { isListedProject } from '@/lib/palette/model';
import type { GenerationSettings } from '@/lib/palette/generation';

type State = {
  ready: boolean;
  projects: Project[];
  versions: Version[];
  assets: Asset[];
  trashed: string[];
  landingGeneration: GenerationSettings;
};
type AssetSnap = { category: string; archivedAt?: number; trashed: boolean };

const SAVE_DELAY = 800;

/**
 * Persists the workspace to the backend by diffing against what the server
 * last saw: projects (debounced PUT per project), asset category / bin /
 * archive changes (PATCH), new Archive versions (POST) and preferences.
 * The browser is the only writer of project documents.
 */
export function useWorkspaceSync(state: State, opts: { onProjectSaved: (id: string, rev: number) => void; onError: (message: string) => void }) {
  const projectSnap = useRef(new Map<string, number>());
  const versionSnap = useRef(new Set<string>());
  const assetSnap = useRef(new Map<string, AssetSnap>());
  const prefsSnap = useRef('');
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const latest = useRef(state);
  const inflight = useRef(new Map<string, Promise<void>>());
  const [pending, setPending] = useState(0);
  latest.current = state;
  const optsRef = useRef(opts);
  optsRef.current = opts;

  /** Record server state as already-synced (call after bootstrap). */
  const baseline = useCallback((s: Pick<State, 'projects' | 'versions' | 'assets' | 'landingGeneration'>) => {
    projectSnap.current = new Map(s.projects.map(p => [p.id, p.updated]));
    versionSnap.current = new Set(s.versions.map(v => v.id));
    assetSnap.current = new Map(s.assets.map(a => [a.id, { category: a.category, archivedAt: a.archivedAt, trashed: !!a.trashed }]));
    prefsSnap.current = JSON.stringify({ landingGeneration: s.landingGeneration });
  }, []);

  const saveNow = useCallback((id: string): Promise<void> => {
    const t = timers.current.get(id);
    if (t) { clearTimeout(t); timers.current.delete(id); }
    const project = latest.current.projects.find(p => p.id === id);
    if (!project) return Promise.resolve();
    if (projectSnap.current.get(id) === project.updated) return inflight.current.get(id) || Promise.resolve();
    const updated = project.updated;
    setPending(n => n + 1);
    const run = (inflight.current.get(id) || Promise.resolve()).then(async () => {
      try {
        const res = await api.saveProject(project);
        projectSnap.current.set(id, updated);
        optsRef.current.onProjectSaved(id, res.rev);
      } catch (e) {
        optsRef.current.onError((e as Error).message);
      } finally {
        setPending(n => n - 1);
      }
    });
    inflight.current.set(id, run);
    return run;
  }, []);

  /** Save immediately (before chat / export / generation reads the doc). */
  const flush = useCallback(async (id?: string) => {
    const ids = id ? [id] : [...timers.current.keys()];
    await Promise.all(ids.map(saveNow));
  }, [saveNow]);

  // Projects → debounced PUT.
  useEffect(() => {
    if (!state.ready) return;
    for (const p of state.projects) {
      const seen = projectSnap.current.get(p.id);
      if (seen === p.updated) continue;
      if (seen === undefined && !isListedProject(p) && !p.updated) continue;
      const t = timers.current.get(p.id);
      if (t) clearTimeout(t);
      timers.current.set(p.id, setTimeout(() => { void saveNow(p.id); }, SAVE_DELAY));
    }
  }, [state.ready, state.projects, saveNow]);

  // Versions → POST new ones.
  useEffect(() => {
    if (!state.ready) return;
    for (const v of state.versions) {
      if (versionSnap.current.has(v.id)) continue;
      versionSnap.current.add(v.id);
      api.createVersion(v).catch(e => { versionSnap.current.delete(v.id); optsRef.current.onError((e as Error).message); });
    }
  }, [state.ready, state.versions]);

  // Assets → PATCH category / archive / bin changes; the same change to several
  // assets (a multi-select action) goes out as one bulk request.
  useEffect(() => {
    if (!state.ready) return;
    const trashed = new Set(state.trashed);
    const groups = new Map<string, { patch: Record<string, any>; items: { id: string; before: AssetSnap }[] }>();
    for (const a of state.assets) {
      const now: AssetSnap = { category: a.category, archivedAt: a.archivedAt, trashed: trashed.has(a.id) };
      const before = assetSnap.current.get(a.id);
      if (!before) { assetSnap.current.set(a.id, now); continue; }
      const patch: Record<string, any> = {};
      if (before.category !== now.category) patch.category = now.category;
      if (!!before.archivedAt !== !!now.archivedAt) patch.archived = !!now.archivedAt;
      if (before.trashed !== now.trashed) patch.trashed = now.trashed;
      if (!Object.keys(patch).length) continue;
      assetSnap.current.set(a.id, now);
      const key = JSON.stringify(patch);
      const group = groups.get(key) || { patch, items: [] };
      group.items.push({ id: a.id, before });
      groups.set(key, group);
    }
    for (const { patch, items } of groups.values()) {
      const request = items.length > 1 ? api.bulkAssets(items.map(i => i.id), patch) : api.patchAsset(items[0].id, patch);
      request.catch(e => { for (const i of items) assetSnap.current.set(i.id, i.before); optsRef.current.onError((e as Error).message); });
    }
  }, [state.ready, state.assets, state.trashed]);

  // Preferences.
  useEffect(() => {
    if (!state.ready) return;
    const json = JSON.stringify({ landingGeneration: state.landingGeneration });
    if (json === prefsSnap.current) return;
    const id = setTimeout(() => {
      prefsSnap.current = json;
      api.savePreferences({ landingGeneration: state.landingGeneration }).catch(() => { prefsSnap.current = ''; });
    }, SAVE_DELAY);
    return () => clearTimeout(id);
  }, [state.ready, state.landingGeneration]);

  // Flush pending saves when the window is hidden or closed.
  useEffect(() => {
    const onHide = () => { if (document.visibilityState === 'hidden') void flush(); };
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('pagehide', onHide);
    return () => { document.removeEventListener('visibilitychange', onHide); window.removeEventListener('pagehide', onHide); };
  }, [flush]);

  /** Forget an asset's snapshot (it was removed server-side). */
  const markAsset = useCallback((a: Asset, trashed = false) => {
    assetSnap.current.set(a.id, { category: a.category, archivedAt: a.archivedAt, trashed });
  }, []);

  return { baseline, flush, saving: pending > 0 || timers.current.size > 0, markAsset };
}
