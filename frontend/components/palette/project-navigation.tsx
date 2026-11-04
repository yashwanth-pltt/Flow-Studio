'use client';
import { useEffect, useState } from 'react';
import { Clapperboard, AudioLines, Film, Grid2X2, ImageIcon, MapPin, PanelLeft, Trash2, Upload, UserRound, Video } from 'lucide-react';
import { Sidebar, SidebarMenu, SidebarMenuButton, SidebarMenuItem } from '@/components/ui/sidebar';
import type { Surface } from '@/lib/palette/model';
import { useI18n } from '@/lib/i18n';
import { getAppViewport } from '@/lib/app-viewport';
type Props={surface:Surface;category:string;onCategory:(category:string)=>void};
const mediaItems=[['All media',Grid2X2],['Images',ImageIcon],['Videos',Video],['Voices & audio',AudioLines],['Characters',UserRound],['Scenes',Clapperboard],['Locations',MapPin],['Uploads',Upload]] as const;
type Mode='expanded'|'collapsed';
export function ProjectNavigation({surface,category,onCategory}:Props){
 const {t}=useI18n();
 const [mode,setMode]=useState<Mode>('collapsed');
 useEffect(()=>{try{const saved=localStorage.getItem('palette-project-navigation');if(saved==='expanded'||saved==='collapsed')setMode(saved)}catch{}},[]);
 const expanded=mode==='expanded';
 function changeMode(next:Mode){setMode(next);try{localStorage.setItem('palette-project-navigation',next)}catch{}}
 function act(fn:()=>void){fn();if(getAppViewport().width<768)changeMode('collapsed')}
 useEffect(()=>{const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'&&getAppViewport().width<768)changeMode('collapsed')};window.addEventListener('keydown',escape);return()=>window.removeEventListener('keydown',escape)},[]);
 return <><Sidebar collapsible="none" className="global-sidebar media-sidebar project-submenu" data-nav-mode={mode} data-expanded={expanded} aria-label={t('Project tools')}>
 <div className="media-brand-row"><button className="media-nav-toggle" aria-label={t('Toggle project menu')} aria-expanded={expanded} title={t(expanded?'Collapse navigation':'Expand navigation')} onClick={()=>changeMode(expanded?'collapsed':'expanded')}><PanelLeft size={16}/></button></div>
 <nav aria-label={t(surface==='Library'?'Library navigation':'Project media navigation')} className="media-nav-body"><div className="media-category-title">{t('Media')}</div><SidebarMenu>{mediaItems.map(([name,Icon])=><SidebarMenuItem key={name}><SidebarMenuButton className="media-nav-item" aria-label={t(name)} title={t(name)} isActive={category===name} onClick={()=>act(()=>onCategory(name))}><Icon size={15}/><span>{t(name)}</span></SidebarMenuButton></SidebarMenuItem>)}</SidebarMenu><div className="media-nav-divider"/><div className="media-category-title">{t('Exports')}</div><SidebarMenu><SidebarMenuItem><SidebarMenuButton className="media-nav-item" aria-label={t(surface==='Library'?'All exported videos':'Exported videos')} title={t(surface==='Library'?'All exported videos':'Exported videos')} isActive={category==='Created videos'} onClick={()=>act(()=>onCategory('Created videos'))}><Film size={15}/><span>{t('Exported videos')}</span></SidebarMenuButton></SidebarMenuItem></SidebarMenu>
 </nav>
 <div className="media-nav-footer"><button className={`media-nav-item ${category==='Bin'?'is-active':''}`} aria-label={t('Bin')} title={t('Bin')} onClick={()=>act(()=>onCategory('Bin'))}><Trash2 size={15}/><span>{t('Bin')}</span></button></div>
 </Sidebar>{expanded&&<button className="sidebar-scrim" aria-label={t('Close project menu')} onClick={()=>changeMode('collapsed')}/>}</>;
}
