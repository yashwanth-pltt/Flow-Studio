'use client';
import { usePointerReorder } from '@/hooks/use-pointer-reorder';
import { ArrowLeft, ArrowRight, Check, FolderOpen, ImageIcon, Mic, MoreHorizontal, Music, Play, Plus, RotateCcw, Settings2, Trash2, UserRound, MapPin } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@/components/ui/dropdown-menu';
import type { Asset, Project } from '@/lib/palette/model';
import { filterStudioMedia } from '@/lib/palette/studio';
import { useI18n } from '@/lib/i18n';

type Props = {
  project: Project; assets: Asset[]; trashed: string[]; category: string; search: string;
  selectedAssets: string[];
  onSelectAsset: (id: string, selected?: boolean) => void; onPreview: (id: string) => void; onUpload: () => void; onLibrary: () => void;
  onReorder: (id: string, target: string) => void;
  onClassify: (id: string) => void; onTrash: (id: string) => void; onRestore: (id: string) => void; onDeleteForever: (id: string) => void;
  onRecord: () => void; onAudio: (track: 'voiceId' | 'musicId', id: string) => void;
};

function Thumb({ asset }: { asset?: Asset }) {
  const { t } = useI18n();
  if (!asset) return <div className="studio-missing"><ImageIcon size={26}/><span>{t('Add media')}</span></div>;
  if (asset.type === 'audio') return <div className="studio-audio-art"><Music size={30}/></div>;
  if (asset.type === 'video' && !asset.poster) return <video src={asset.src} muted preload="metadata"/>;
  return <img src={asset.poster || asset.src} alt={asset.name} loading="lazy" draggable={false}/>;
}

export function StudioBoard(props: Props) {
  const { project, assets, category, search, trashed } = props;
  const { t } = useI18n();
  const media = filterStudioMedia(assets, category, search, trashed, project);
  const reorder = usePointerReorder(media.map(a=>a.id), props.onReorder, '.studio-board-scroll');
  const audio = category === 'Voices & audio';
  const EmptyIcon = category === 'Characters' ? UserRound : category === 'Locations' ? MapPin : audio ? Music : FolderOpen;
  const emptyTitle = t(search ? 'No results found' : category === 'Characters' ? 'Characters for your story' : category === 'Locations' ? 'Where your story takes place' : category === 'Scenes' ? 'No scene stills yet' : audio ? 'Give your story a voice' : category === 'Bin' ? 'Bin is empty' : 'Add media to this project');
  return <section id="studio-media-panel" className="studio-board" role="tabpanel" aria-labelledby="studio-media-tab">
    <div className="studio-board-scroll">
      <div className="studio-board-heading"><span>{t(category)}</span><small>{t(media.length===1?'{n} asset':'{n} assets',{n:media.length})}</small>{audio&&<button className="text-button" onClick={props.onRecord}><Mic size={13}/>{t('Record voice')}</button>}</div>
      <div data-reorder-scope="media" className={`studio-media-grid ${category === 'Characters' ? 'is-characters' : ''}`}>
        {media.map((asset,index) => <article key={asset.id} data-studio-asset={asset.id} data-reorder-id={asset.id} style={reorder.style(asset.id)} className={`studio-asset ${props.selectedAssets.includes(asset.id) ? 'is-selected' : ''} ${reorder.className(asset.id)}`}>
          <button className="studio-asset-preview" aria-label={t('Select media {name}',{name:asset.name})} aria-pressed={props.selectedAssets.includes(asset.id)} title={t('Click to select · drag to reorder')} {...reorder.bind(asset.id)} onClick={e => {if(!reorder.consumeClick(e))props.onSelectAsset(asset.id)}}><Thumb asset={asset}/><span className="studio-media-type">{asset.type==='video'?<Play size={12}/>:asset.type==='audio'?<Music size={12}/>:<ImageIcon size={12}/>}</span>{asset.duration&&<span className="studio-asset-duration">{asset.duration.toFixed(1)}s</span>}{props.selectedAssets.includes(asset.id)&&<span className="studio-asset-check"><Check size={12}/></span>}</button>
          {asset.type==='audio'&&<audio controls preload="metadata" src={asset.src} aria-label={t('Play {name}',{name:asset.name})}/>}
          <div className="studio-asset-actions"><DropdownMenu><DropdownMenuTrigger asChild><button className="icon-button studio-asset-menu" aria-label={t('{name} actions',{name:asset.name})}><MoreHorizontal size={15}/></button></DropdownMenuTrigger><DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => props.onPreview(asset.id)}><Play size={13}/>{t('Preview')}</DropdownMenuItem>
            {category==='Bin'?<><DropdownMenuItem onClick={() => props.onRestore(asset.id)}><RotateCcw size={13}/>{t('Restore')}</DropdownMenuItem><DropdownMenuItem onClick={() => props.onDeleteForever(asset.id)}><Trash2 size={13}/>{t('Delete forever')}</DropdownMenuItem></>:<>
              <DropdownMenuItem onClick={() => props.onSelectAsset(asset.id, true)}><Check size={13}/>{t('Select')}</DropdownMenuItem>
              <DropdownMenuItem disabled={index===0} onClick={()=>props.onReorder(asset.id,media[index-1].id)}><ArrowLeft size={13}/>{t('Move earlier')}</DropdownMenuItem>
              <DropdownMenuItem disabled={index===media.length-1} onClick={()=>props.onReorder(asset.id,media[index+1].id)}><ArrowRight size={13}/>{t('Move later')}</DropdownMenuItem>
              <DropdownMenuItem onClick={() => props.onClassify(asset.id)}><Settings2 size={13}/>{t('Change category')}</DropdownMenuItem>
              <DropdownMenuItem onClick={() => props.onTrash(asset.id)}><Trash2 size={13}/>{t('Move to Bin')}</DropdownMenuItem>
            </>}
          </DropdownMenuContent></DropdownMenu></div>
        </article>)}
      </div>
      {!media.length&&<div className="studio-board-empty"><EmptyIcon size={30}/><h2>{emptyTitle}</h2>{!search&&category!=='Bin'&&<><p>{t(category==='Characters'?'Add character references, then set their category to Characters from the media menu.':category==='Locations'?'Add location references, then set their category to Locations.':category==='Scenes'?'Generated scene stills are filed here.':audio?'Collect voices and music, then choose the project voice-over and music below.':'Upload files or bring them in from the Library, then add them to the storyboard.')}</p><div><button className="secondary" onClick={props.onUpload}><Plus size={14}/>{t('Upload')}</button><button className="text-button" onClick={props.onLibrary}><FolderOpen size={14}/>{t('Library')}</button></div></>}</div>}
      {audio&&<section className="studio-scene-audio"><h3>{t('Project audio')}</h3>{(['voiceId','musicId'] as const).map(track=><label key={track}>{t(track==='voiceId'?'Voice-over':'Music & ambience')}<select aria-label={t(track==='voiceId'?'Voice-over':'Music & ambience')} value={project[track]||''} onChange={e=>props.onAudio(track,e.target.value)}><option value="">{t('None')}</option>{assets.filter(a=>a.type==='audio'&&!trashed.includes(a.id)).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>)}</section>}
    </div>


  </section>;
}
