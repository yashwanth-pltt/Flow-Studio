'use client';

import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { ImageIcon, Sparkles, Type, Replace, Volume2, Film, Scan } from 'lucide-react';
import type { Asset, Scene } from '@/lib/palette/model';
import { useI18n } from '@/lib/i18n';

type Props = {
  timedCaption?: string; ratio?: string; asset?: Asset; scene?: Scene; videoRef: RefObject<HTMLVideoElement | null>;
  playing: boolean; playhead: number; sourceVolume: number;
  onPosition: (position:{x:number;y:number}) => void; onScale: (scale: number) => void; onEdit: () => void;
  onTool: (tool: string) => void; onReplace: () => void;
  onError: () => void;
};

export function EditingCanvas({timedCaption,ratio='16:9',asset,scene,videoRef,playing,playhead,sourceVolume,onPosition,onScale,onEdit,onTool,onReplace,onError}:Props) {
  const { t } = useI18n();
  const board = useRef<HTMLDivElement>(null);
  const drag = useRef<{origin:number;axis:'x'|'y';scale:number;direction:number} | null>(null);
  const move=useRef<{pointer:number;x:number;y:number;position:{x:number;y:number}}|null>(null);
  const position=scene?.position??{x:0,y:0};
  const [moving,setMoving]=useState(false);
  const [dimensions,setDimensions] = useState({width:788,height:1000});
  const [selection,setSelection] = useState(true);
  const scale = scene?.scale ?? 1;
  useEffect(()=>setSelection(true),[scene?.id]);
  useEffect(()=>{if(videoRef.current){videoRef.current.currentTime=(scene?.sourceIn||0)+playhead;videoRef.current.volume=sourceVolume;if(playing)void videoRef.current.play().catch(onError);else videoRef.current.pause()}},[playing,asset?.id,scene?.id,sourceVolume,scene?.sourceIn]);
  const [ratioWidth,ratioHeight]=ratio.split(':').map(Number);
  const boardWidth=720*ratioWidth/ratioHeight,boardHeight=720;
  const fit = Math.min(boardWidth/dimensions.width,boardHeight/dimensions.height);
  const width = dimensions.width*fit/boardWidth*100*scale;
  const height = dimensions.height*fit/boardHeight*100*scale;
  const motion = scene?.motion==='Push in'?`scale(${1+playhead/Math.max(scene.duration,1)*.12})`:scene?.motion==='Pan left'?`translateX(${(1-playhead/Math.max(scene.duration,1))*6}%)`:'none';
  return <section className="editing-workspace" aria-label={t('Media editing canvas')}>
    <div className="editor-artboard" style={{aspectRatio:`${ratioWidth}/${ratioHeight}`,width:`min(100cqw, calc(100cqh * ${ratioWidth} / ${ratioHeight}))`}} ref={board} onClick={()=>setSelection(false)}>
      {asset&&asset.type!=='audio'?<div className={`media-selection ${selection?'is-selected':''}`} style={{width:`${width}%`,height:`${height}%`,left:`${50+position.x*100}%`,top:`${50+position.y*100}%`,cursor:moving?'grabbing':'grab'}} tabIndex={0} aria-label={t('Move media on canvas')} title={t('Drag to move · arrow keys to nudge')} onDragStart={e=>e.preventDefault()} onPointerDown={e=>{if(e.button!==0||!e.isPrimary||(e.target as Element).closest('button'))return;e.stopPropagation();e.currentTarget.setPointerCapture(e.pointerId);move.current={pointer:e.pointerId,x:e.clientX,y:e.clientY,position};setSelection(true);setMoving(true)}} onPointerMove={e=>{const m=move.current;if(!m||m.pointer!==e.pointerId)return;e.preventDefault();const bounds=board.current!.getBoundingClientRect();onPosition({x:Math.max(-.5,Math.min(.5,m.position.x+(e.clientX-m.x)/bounds.width)),y:Math.max(-.5,Math.min(.5,m.position.y+(e.clientY-m.y)/bounds.height))})}} onPointerUp={()=>{move.current=null;setMoving(false)}} onPointerCancel={()=>{if(move.current)onPosition(move.current.position);move.current=null;setMoving(false)}} onLostPointerCapture={()=>{move.current=null;setMoving(false)}} onKeyDown={e=>{if(e.target!==e.currentTarget)return;if(e.key==='Escape'&&move.current){onPosition(move.current.position);move.current=null;setMoving(false)}if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const step=e.shiftKey?.02:.005;onPosition({x:Math.max(-.5,Math.min(.5,position.x+(e.key==='ArrowLeft'?-step:e.key==='ArrowRight'?step:0))),y:Math.max(-.5,Math.min(.5,position.y+(e.key==='ArrowUp'?-step:e.key==='ArrowDown'?step:0)))})}}} onClick={e=>{e.stopPropagation();setSelection(true)}}>
        <div className="media-viewport">
          {asset.type==='video'?<video key={asset.id} ref={videoRef} src={asset.src} poster={asset.poster} playsInline onLoadedMetadata={e=>{const v=e.currentTarget;setDimensions({width:v.videoWidth,height:v.videoHeight});v.volume=sourceVolume;v.currentTime=(scene?.sourceIn||0)+playhead;if(playing)void v.play().catch(onError)}} onError={onError} style={{transform:motion,opacity:scene?.transition==='Fade'&&playing?Math.min(playhead*2,1):1}}/>:<img src={asset.src} alt={asset.name} onLoad={e=>setDimensions({width:e.currentTarget.naturalWidth,height:e.currentTarget.naturalHeight})} style={{transform:motion,opacity:scene?.transition==='Fade'&&playing?Math.min(playhead*2,1):1}}/>}
        </div>
        {selection&&<>{['nw','n','ne','e','se','s','sw','w'].map(position=><button key={position} className={`selection-handle handle-${position}`} aria-label={t('Resize media {pos}',{pos:position})} title={t('Drag to resize · arrow keys to adjust')} onPointerDown={e=>{e.stopPropagation();e.currentTarget.setPointerCapture(e.pointerId);drag.current={origin:['e','w'].includes(position)?e.clientX:e.clientY,axis:['e','w'].includes(position)?'x':'y',scale,direction:position.includes('n')||position==='w'?-1:1}}} onPointerMove={e=>{if(!drag.current)return;const delta=((drag.current.axis==='x'?e.clientX:e.clientY)-drag.current.origin)/((drag.current.axis==='x'?board.current?.clientWidth:board.current?.clientHeight)||400)*drag.current.direction;onScale(Math.max(.3,Math.min(1,drag.current.scale+delta)))}} onPointerUp={()=>{drag.current=null}} onPointerCancel={()=>{drag.current=null}} onKeyDown={e=>{if(['ArrowUp','ArrowRight','ArrowDown','ArrowLeft'].includes(e.key)){e.preventDefault();onScale(Math.min(1,Math.max(.3,scale+(['ArrowUp','ArrowRight'].includes(e.key)?.05:-.05))))}}}/> )}</>}
      </div>:<div className="empty-stage"><ImageIcon size={32}/><button className="secondary" onClick={onReplace}>{t('Add media')}</button></div>}
      {(timedCaption||scene?.caption)&&<div className="caption-overlay">{timedCaption||scene?.caption}</div>}
      <span className="artboard-ratio">{ratio}</span>
    {asset&&<div className="floating-edit-toolbar" onClick={e=>e.stopPropagation()} aria-label={t('Selected media tools')}><button className="edit-with-agent" onClick={onEdit}><Sparkles size={15}/> {t('Edit with Palette')}</button><span className="toolbar-divider"/>{[{name:'Script',label:t('Script'),icon:Type},{name:'Replace',label:t('Replace media'),icon:Replace},{name:'Sound',label:t('Audio levels'),icon:Volume2},{name:'Motion',label:t('Motion'),icon:Film}].map(({name,label,icon:Icon})=><button key={name} aria-label={label} title={label} className="icon-button" onClick={()=>name==='Replace'?onReplace():onTool(name)}><Icon size={16}/></button>)}<button className="icon-button" aria-label={t('Fit media')} title={t('Fit media')} onClick={()=>{onScale(1);onPosition({x:0,y:0})}}><Scan size={16}/></button></div>}
    </div>
  </section>;
}
