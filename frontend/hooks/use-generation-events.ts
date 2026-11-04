'use client';
import { useEffect, useRef } from 'react';
import { api, browserAsset, type GenerationItem } from '@/lib/api';
import { sseUrl } from '@/lib/api-helper';

const TERMINAL = new Set(['completed', 'failed']);
const POLL_MS = 4000;
// A generation the server has no record of (its job died before saving
// anything) would otherwise be polled forever; give up after ~2 minutes.
const MISSING_LIMIT = 30;

/**
 * Delivers generation updates (scene stills, clips, plans, music, exports).
 *
 * Primary channel: the per-user SSE stream `/events/{userId}`. Reliability
 * net: while any generation is pending, poll `/generations/{id}` — SSE runs
 * on an in-process queue, so a multi-worker host (or a reload mid-job) can
 * miss events; polling also resumes jobs saved in project docs.
 */
export function useGenerationEvents(
  userId: string | null,
  pendingIds: string[],
  onItem: (item: GenerationItem) => void,
  onProgress?: (item: GenerationItem) => void,
) {
  const handled = useRef(new Set<string>());
  const missing = useRef(new Map<string, number>());
  const cb = useRef({ onItem, onProgress });
  cb.current = { onItem, onProgress };

  const dispatch = (item: GenerationItem) => {
    if (!item?.id || !item.status) return;
    if (!TERMINAL.has(item.status)) { cb.current.onProgress?.(item); return; }
    const key = `${item.id}:${item.status}`;
    if (handled.current.has(key)) return;
    handled.current.add(key);
    cb.current.onItem({ ...item, asset: item.asset ? browserAsset(item.asset) : undefined });
  };

  useEffect(() => {
    if (!userId) return;
    let es: EventSource | null = null;
    let cancelled = false;
    const onMessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as GenerationItem;
        if (data.status === 'connected') return;
        dispatch(data);
      } catch { /* ignore malformed */ }
    };
    sseUrl(userId, 'video').then(({ url, withCredentials }) => {
      if (cancelled) return;
      es = new EventSource(url, { withCredentials });
      es.addEventListener('video_generation', onMessage);
    });
    return () => { cancelled = true; es?.close(); };
  }, [userId]);

  const key = [...new Set(pendingIds)].sort().join(',');
  useEffect(() => {
    if (!key) return;
    const ids = key.split(',');
    let stop = false;
    const tick = async () => {
      for (const id of ids) {
        if (stop) return;
        try {
          const res = await api.generation(id);
          if (res.status === 'missing') {
            const n = (missing.current.get(id) || 0) + 1;
            missing.current.set(id, n);
            if (n >= MISSING_LIMIT) dispatch({ id, generation_id: id, status: 'failed', error: 'This generation was lost. Please try again.' } as GenerationItem);
            continue;
          }
          missing.current.delete(id);
          for (const item of res.items) {
            const asset = res.assets.find(a => a.id === (item as any).asset_id);
            dispatch({ ...item, asset });
          }
        } catch { /* transient; retry next tick */ }
      }
    };
    const first = setTimeout(tick, 1500);
    const timer = setInterval(tick, POLL_MS);
    return () => { stop = true; clearTimeout(first); clearInterval(timer); };
  }, [key]);
}
