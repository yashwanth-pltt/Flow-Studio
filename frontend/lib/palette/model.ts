import type { CameraMotion } from './camera-motion';
import type { GenerationSettings } from './generation';
export type Surface='Library'|'Project'|'Archive'|'Studio'|'Production';
export type AssetSource='upload'|'recording'|'generated'|'camera'|'tts'|'music'|'export';
export type Asset={id:string;name:string;type:'image'|'video'|'audio';src:string;poster?:string;duration?:number;width?:number;height?:number;size?:number;category:string;uploaded?:boolean;source?:AssetSource;projectId?:string;createdProjectId?:string;createdAt?:number;archivedAt?:number;trashed?:boolean;analysis?:{kind:string;name:string;description:string}|null};
/** In-flight generation for a scene (stored in the project doc so a reload resumes it). */
/** `next`: what to do once the still is ready — animate it now, or wait for the rest of a Flow batch. */
export type SceneGen={stage:'image'|'video';status:'pending'|'failed';generationId:string;next?:'video'|'flow';error?:string};
export type Scene={id:string;name:string;assetId:string;duration:number;sourceIn?:number;script:string;caption:string;motion:'Still'|'Push in'|'Pan left';transition:'Cut'|'Fade';cameraMotion?:CameraMotion;scale?:number;position?:{x:number;y:number};prompt?:string;videoPrompt?:string;engine?:string;referenceAssetIds?:string[];gen?:SceneGen;/** Explicit last frame for this scene's clip (an image or video asset). */endAssetId?:string};
export type Message={id:string;role:'user'|'agent';text:string;generation?:GenerationSettings};
export type TimedCaption={id:string;text:string;start:number;end:number};
/** Planning / music jobs tracked on the project. */
export type ProjectJobs={plan?:{generationId:string;status:'pending'|'failed';mode:'auto'|'manual';brief:string;error?:string};music?:{generationId:string;status:'pending'|'failed';error?:string};/** Flow batch: scenes whose stills must all exist before their chained clips start. */flow?:{sceneIds:string[];/** 'clips': the clips run one after another, each starting on the previous clip's last frame. */phase?:'stills'|'clips';index?:number;/** the current clip's start frame, and whether it was retried */start?:string;retried?:boolean};/** Auto mode: export the finished film once every clip is done. */auto?:{export:boolean};/** Set from the moment generation is requested until its jobs have started (prompt writing, saving),
 so the UI never shows an idle "Generate" state in between. */preparing?:boolean};
export type Project={id:string;name:string;scenes:Scene[];assetIds?:string[];assetOrder?:string[];captions?:TimedCaption[];generation?:GenerationSettings;messages:Message[];updated:number;rev?:number;musicId?:string;voiceId?:string;audioMix?:{voice:number;music:number;source:number};story?:string;bible?:Record<string,string>;jobs?:ProjectJobs;/** Older projects only: frames once chosen in the composer (no longer set — frames are generated). */firstFrameId?:string;lastFrameId?:string};
export type Version={id:string;name:string;date:number;project:Project};
export const createScene=(assetId='',name='Scene 01'):Scene=>({id:crypto.randomUUID(),name,assetId,duration:5,script:'',caption:'',motion:'Still',transition:'Cut'});
/** Placeholder so the workspace always has a current project; it is only saved once edited. */
export const blankProject=(name='Untitled project'):Project=>({id:crypto.randomUUID(),name,updated:0,scenes:[],assetIds:[],assetOrder:[],messages:[]});
/** Whether a project should appear in project lists (saved, or has content). */
export const isListedProject=(p:Project)=>p.rev!==undefined||p.scenes.length>0||p.messages.length>0;
export function downloadBlob(blob:Blob,name:string){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000)}
export function srtTime(seconds:number){return new Date(seconds*1000).toISOString().slice(11,23).replace('.',',')}
