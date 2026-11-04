'use client';
import { usePointerReorder } from '@/hooks/use-pointer-reorder';
import { Clapperboard, Copy, Film, ImageIcon, Link2, MoreHorizontal, Pencil, Play, Plus, Replace, RotateCcw, Sparkles, Trash2 } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import type { Asset, Project, Scene } from '@/lib/palette/model';
import { useI18n } from '@/lib/i18n';
import { userError } from '@/lib/palette/user-error';

type Props = {
  project: Project; assets: Asset[]; trashed: string[]; selected?: string; planning: boolean; flow: boolean;
  onOpen: (scene: Scene) => void; onDetails: (scene: Scene) => void; onChange: (id: string, patch: Partial<Scene>) => void;
  onMove: (id: string, index: number) => void; onAdd: () => void; onReplace: (scene: Scene) => void;
  onGenerate: (scene: Scene, stage: 'image' | 'video') => void; onRetry: (scene: Scene) => void;
  onDuplicate: (scene: Scene) => void; onRemove: (scene: Scene) => void; onRestoreMedia: (id: string) => void; onGenerateAll: () => void;
  /** The project is generating (incl. preparing between steps): no Generate buttons, just status. */
  busy?: boolean;
};

/** The project's scenes as cards, in play order: what the story is before it is edited. */
export function StoryboardGrid(props: Props) {
  const { project, assets, trashed, selected, planning, flow } = props;
  const { t } = useI18n();
  const scenes = project.scenes;
  const reorder = usePointerReorder(scenes.map(s => s.id), (id, target) => props.onMove(id, scenes.findIndex(s => s.id === target)), '.studio-board-scroll');
  const total = scenes.reduce((n, s) => n + s.duration, 0);
  const waiting = scenes.filter(s => !s.assetId && !s.gen).length, running = scenes.filter(s => s.gen?.status === 'pending').length;
  const busy = !!props.busy || running > 0;
  return <section id="studio-storyboard-panel" className="studio-board" role="tabpanel" aria-labelledby="studio-storyboard-tab">
    <div className="studio-board-scroll">
      <div className="studio-board-heading"><span>{t('Storyboard')}</span><small>{t('{n} scenes · {s}s', { n: scenes.length, s: total.toFixed(1) })}</small></div>
      {!planning && (waiting > 0 || busy) && <div className="storyboard-next-step" role="status">{busy ? <><span className="spinner"/><span>{running === 1 ? t('Generating 1 scene…') : running > 0 ? t('Generating {n} scenes…', { n: running }) : t('Preparing generation…')}</span></> : <><span>{t(waiting > 1 ? '{n} scenes need a still and a clip.' : '1 scene needs a still and a clip.', { n: waiting })}</span><button className="primary" onClick={props.onGenerateAll}><Sparkles size={14}/>{t('Generate')}</button></>}</div>}
      {!scenes.length && !planning ? <div className="studio-board-empty"><Clapperboard size={30}/><h2>{t('No scenes yet')}</h2><p>{t('Describe your video in the chat to plan scenes, or add a scene from your media.')}</p><div><button className="secondary" onClick={props.onAdd}><Plus size={14}/>{t('Add scene')}</button></div></div> :
      <div className="studio-story-grid" data-reorder-scope="storyboard">
        {scenes.map((s, index) => {
          const a = assets.find(x => x.id === s.assetId), binned = !!a && trashed.includes(a.id);
          return <article key={s.id} data-reorder-id={s.id} style={reorder.style(s.id)} className={`studio-story-card ${s.id === selected ? 'is-selected' : ''}${reorder.className(s.id)}`}>
            <button className="studio-story-preview" aria-label={t('Open scene {n}: {name}', { n: index + 1, name: s.name })} title={t('Click to open details · drag to reorder')} {...reorder.bind(s.id)} onClick={e => { if (!reorder.consumeClick(e)) props.onDetails(s); }}>
              {a && !binned ? a.type === 'video' && !a.poster ? <video src={a.src} muted preload="metadata"/> : <img src={a.poster || a.src} alt="" draggable={false}/> : <div className="studio-missing">{binned ? <><Trash2 size={22}/><span>{t('Media is in the Bin')}</span></> : <><ImageIcon size={22}/><span>{t('No media yet')}</span></>}</div>}
              {s.gen && <span className={`scene-gen-badge ${s.gen.status}`}>{s.gen.status === 'pending' ? <span className="spinner"/> : null}{t(s.gen.status === 'failed' ? 'Failed' : s.gen.stage === 'image' ? 'Still…' : 'Clip…')}</span>}
              <span className="studio-scene-number">{String(index + 1).padStart(2, '0')} · {s.duration.toFixed(1)}s</span>
            </button>
            <div className="studio-story-details">
              <input aria-label={t('Scene {n} name', { n: index + 1 })} value={s.name} onChange={e => props.onChange(s.id, { name: e.target.value })}/>
              {flow && index > 0 && <div className="story-flow-marks"><span title={t('Starts where scene {n} ends', { n: index })}><Link2 size={11}/>{t('Continues')}</span></div>}
              <textarea aria-label={t('Scene {n} script', { n: index + 1 })} placeholder={t('Script for this scene…')} value={s.script} onChange={e => props.onChange(s.id, { script: e.target.value })}/>
            </div>
            {s.gen?.status === 'failed' && <p className="scene-gen-error">{s.gen.error ? t(userError(s.gen.error)) : t('Generation failed.')}</p>}
            <div className="studio-scene-actions">
              {binned ? <button className="text-button" onClick={() => props.onRestoreMedia(a!.id)}><RotateCcw size={13}/>{t('Restore media')}</button> :
               s.gen?.status === 'failed' ? <button className="text-button" onClick={() => props.onRetry(s)}><RotateCcw size={13}/>{t('Retry')}</button> :
               <button className="text-button" onClick={() => props.onOpen(s)}><Play size={13}/>{t('Edit in Studio')}</button>}
              <DropdownMenu><DropdownMenuTrigger asChild><button className="icon-button" aria-label={t('Scene {n} actions', { n: index + 1 })}><MoreHorizontal size={15}/></button></DropdownMenuTrigger><DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => props.onDetails(s)}><Pencil size={13}/>{t('Scene details')}</DropdownMenuItem>
                <DropdownMenuItem disabled={busy} onClick={() => props.onGenerate(s, 'image')}><ImageIcon size={13}/>{t(a ? 'Regenerate still' : 'Generate still')}</DropdownMenuItem>
                <DropdownMenuItem disabled={busy} onClick={() => props.onGenerate(s, 'video')}><Film size={13}/>{t('Animate clip')}</DropdownMenuItem>
                <DropdownMenuItem onClick={() => props.onReplace(s)}><Replace size={13}/>{t('Replace media')}</DropdownMenuItem>
                <DropdownMenuItem onClick={() => props.onDuplicate(s)}><Copy size={13}/>{t('Duplicate')}</DropdownMenuItem>
                <DropdownMenuItem onClick={() => props.onRemove(s)}><Trash2 size={13}/>{t('Remove scene')}</DropdownMenuItem>
              </DropdownMenuContent></DropdownMenu>
            </div>
          </article>;
        })}
        {planning && [0, 1, 2].map(i => <div key={i} className="studio-story-card" aria-hidden="true"><Skeleton className="studio-story-skeleton"/></div>)}
        {!planning && <button className="studio-add-scene" onClick={props.onAdd}><Plus size={20}/><span>{t('Add scene')}</span></button>}
      </div>}
      {planning && <p className="studio-planning-note" role="status"><span className="spinner"/>{t('Planning scenes…')}</p>}
    </div>
  </section>;
}
