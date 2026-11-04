'use client';
import { Archive, Film, MoreHorizontal, Play, RotateCcw, Trash2, Download } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@/components/ui/dropdown-menu';
import type { Asset } from '@/lib/palette/model';
import { useI18n } from '@/lib/i18n';

type Props={videos:Asset[];archive?:boolean;search?:string;onPreview:(id:string)=>void;onArchive:(id:string)=>void;onDelete:(id:string)=>void};
export function CreatedVideos({videos,archive=false,search='',onPreview,onArchive,onDelete}:Props){
 const {t,locale}=useI18n();
 const visible=videos.filter(a=>a.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
 return <section className="created-videos" aria-label={t(archive?'Archived videos':'Created videos')}>
  <header><h2>{t(archive?'Archived videos':'Exports')}</h2><span>{t(visible.length===1?'{n} video':'{n} videos',{n:visible.length})}</span></header>
  <div className="created-video-grid">{visible.map(a=><article className="created-video-card" key={a.id}>
   <button className="created-video-preview" aria-label={t('Preview created video {name}',{name:a.name})} onClick={()=>onPreview(a.id)}><video src={a.src} poster={a.poster} muted playsInline preload="metadata"/><span className="created-video-play"><Play size={18}/></span><span className="created-video-duration">{a.duration?.toFixed(1)}s</span></button>
   <DropdownMenu><DropdownMenuTrigger asChild><button className="created-video-menu icon-button" aria-label={t('{name} video actions',{name:a.name})}><MoreHorizontal size={17}/></button></DropdownMenuTrigger><DropdownMenuContent align="end">
    <DropdownMenuItem onClick={()=>onPreview(a.id)}><Play size={14}/>{t('Preview')}</DropdownMenuItem>
    <DropdownMenuItem onClick={()=>onArchive(a.id)}>{archive?<RotateCcw size={14}/>:<Archive size={14}/>}{t(archive?'Unarchive':'Move to Archive')}</DropdownMenuItem>
    <DropdownMenuItem asChild><a href={a.src} download={`${a.name}.mp4`}><Download size={14}/>{t('Download')}</a></DropdownMenuItem>
    <DropdownMenuItem onClick={()=>onDelete(a.id)}><Trash2 size={14}/>{t('Move to Bin')}</DropdownMenuItem>
   </DropdownMenuContent></DropdownMenu>
   <div className="created-video-info"><strong>{a.name}</strong><small>{a.createdAt?new Date(a.createdAt).toLocaleString(locale):''}</small></div>
  </article>)}</div>
  {!visible.length&&<div className="empty-state"><Film size={30}/><h2>{t(search?'No results found':archive?'No archived videos':'No exports yet')}</h2><p>{t(search?'Try searching for a different name.':archive?'Archive an exported video to keep it here.':'Export the video from the Studio tab and it will appear here.')}</p></div>}
 </section>;
}
