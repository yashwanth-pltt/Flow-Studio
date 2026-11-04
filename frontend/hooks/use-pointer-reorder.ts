'use client';
import { useEffect, useRef, useState, type CSSProperties, type PointerEvent, type MouseEvent, type DragEvent } from 'react';
import { getAppViewport } from '@/lib/app-viewport';

type Drag = { id:string; target:string; dx:number; dy:number };
type Pending = { id:string; pointer:number; x:number; y:number; clientX:number; clientY:number; active:boolean; target:string; element:HTMLElement; scope:HTMLElement; origin:DOMRect; scroll:HTMLElement|null };

/** Shared direct manipulation for grids, lists and the horizontal filmstrip. */
export function usePointerReorder(ids:string[], onMove:(id:string,target:string)=>void, scrollSelector:string) {
  const [drag,setDrag] = useState<Drag|null>(null);
  const pending=useRef<Pending|null>(null), suppress=useRef(false), frame=useRef(0);
  const current=useRef({ids,onMove});current.current={ids,onMove};
  const cancel=()=>{const p=pending.current;pending.current=null;cancelAnimationFrame(frame.current);if(p?.element.hasPointerCapture(p.pointer))p.element.releasePointerCapture(p.pointer);setDrag(null)};
  useEffect(()=>{const escape=(e:KeyboardEvent)=>{if(e.key==='Escape')cancel()};window.addEventListener('keydown',escape);return()=>{window.removeEventListener('keydown',escape);cancelAnimationFrame(frame.current);pending.current=null}},[]);
  const locate=(p:Pending)=>{
    const hit=document.elementsFromPoint(p.clientX,p.clientY).map(el=>el.closest<HTMLElement>('[data-reorder-id]')).find(el=>el&&el.dataset.reorderId!==p.id&&el.closest('[data-reorder-scope]')===p.scope);
    const bounds=p.scope.getBoundingClientRect();
    const inside=(r:DOMRect)=>p.clientX>=r.left&&p.clientX<=r.right&&p.clientY>=r.top&&p.clientY<=r.bottom;
    if(!inside(bounds)||inside(p.origin))p.target=p.id;
    else if(hit&&current.current.ids.includes(hit.dataset.reorderId!))p.target=hit.dataset.reorderId!;
    const {scale}=getAppViewport();setDrag({id:p.id,target:p.target,dx:(p.clientX-p.x)/scale,dy:(p.clientY-p.y)/scale});
  };
  const scroll=()=>{const p=pending.current;if(!p?.active)return;if(p.scroll){const r=p.scroll.getBoundingClientRect(),speed=(v:number,min:number,max:number)=>v<min+32?-12:v>max-32?12:0;p.scroll.scrollBy(speed(p.clientX,r.left,r.right),speed(p.clientY,r.top,r.bottom));locate(p)}frame.current=requestAnimationFrame(scroll)};
  return {
    style:(id:string):CSSProperties|undefined=>drag?.id===id?{transform:`translate(${drag.dx}px, ${drag.dy}px)`,zIndex:20,pointerEvents:'none'}:undefined,
    className:(id:string)=>drag?.id===id?' pointer-reordering':drag?.target===id?' pointer-drop-target':'',
    consumeClick:(e:MouseEvent)=>{if(suppress.current&&e.detail!==0){suppress.current=false;e.preventDefault();e.stopPropagation();return true}return false},
    bind:(id:string)=>({
      onPointerDown:(e:PointerEvent<HTMLElement>)=>{
        if(e.button!==0||!e.isPrimary)return;
        const interactive=(e.target as Element).closest('input,textarea,select,button,a,[role="slider"]');
        if(interactive&&interactive!==e.currentTarget&&!interactive.hasAttribute('data-reorder-handle'))return;
        const scope=e.currentTarget.closest<HTMLElement>('[data-reorder-scope]');if(!scope)return;
        cancel();suppress.current=false;
        pending.current={id,pointer:e.pointerId,x:e.clientX,y:e.clientY,clientX:e.clientX,clientY:e.clientY,active:false,target:id,element:e.currentTarget,scope,origin:e.currentTarget.closest<HTMLElement>('[data-reorder-id]')!.getBoundingClientRect(),scroll:e.currentTarget.closest<HTMLElement>(scrollSelector)};
        e.currentTarget.setPointerCapture(e.pointerId);
      },
      onPointerMove:(e:PointerEvent<HTMLElement>)=>{const p=pending.current;if(!p||p.pointer!==e.pointerId)return;p.clientX=e.clientX;p.clientY=e.clientY;if(!p.active){if(Math.hypot(p.clientX-p.x,p.clientY-p.y)<5)return;p.active=true;suppress.current=true;frame.current=requestAnimationFrame(scroll)}e.preventDefault();locate(p)},
      onPointerUp:(e:PointerEvent<HTMLElement>)=>{const p=pending.current;if(!p||p.pointer!==e.pointerId)return;if(p.active&&p.target!==p.id)current.current.onMove(p.id,p.target);cancel()},
      onPointerCancel:cancel,
      onLostPointerCapture:cancel,
      onDragStart:(e:DragEvent)=>e.preventDefault(),
    }),
  };
}
