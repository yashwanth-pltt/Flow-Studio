export const cameraPresets=['Dolly in','Dolly out','Spiral in','Crane up','Orbit 360° right','Orbit 360° left','Half orbit right','Half orbit left','High orbit right','Low orbit right','Orbit 360° spiral in','Swing','Custom path'] as const;
export type CameraPreset=typeof cameraPresets[number];
export type CameraKeyframe={id:string;time:number;orbit:number;elevation:number;distance:number};
export type CameraMotion={assetId:string;preset:CameraPreset;duration:number;resolution:'480P'|'768P'|'1080P';instructions:string;qualityRetry:boolean;keyframes:CameraKeyframe[]};
export const clamp=(v:number,min:number,max:number)=>Math.max(min,Math.min(max,v));
export function presetKeyframes(preset:CameraPreset):CameraKeyframe[]{
 const poses:Record<CameraPreset,number[][]>={
  'Dolly in':[[0,0,1.7],[0,0,1.2],[0,0,.7]],'Dolly out':[[0,0,.7],[0,0,1.2],[0,0,1.7]],
  'Spiral in':[[0,0,1.8],[60,12,1.2],[120,25,.7]],'Crane up':[[0,-15,1],[0,15,1],[0,45,1]],
  'Orbit 360° right':[[0,0,1],[180,0,1],[360,0,1]],'Orbit 360° left':[[0,0,1],[-180,0,1],[-360,0,1]],
  'Half orbit right':[[0,0,1],[90,0,1],[180,0,1]],'Half orbit left':[[0,0,1],[-90,0,1],[-180,0,1]],
  'High orbit right':[[0,35,1],[90,35,1],[180,35,1]],'Low orbit right':[[0,-20,1],[90,-20,1],[180,-20,1]],
  'Orbit 360° spiral in':[[0,0,1.8],[180,15,1.2],[360,30,.7]],'Swing':[[-45,0,1],[0,10,1],[45,0,1]],
  'Custom path':[[0,0,1],[45,0,1],[90,0,1]],
 };
 return poses[preset].map(([orbit,elevation,distance],i)=>({id:crypto.randomUUID(),time:i*50,orbit,elevation,distance}));
}
export function defaultCameraMotion(assetId:string):CameraMotion{return {assetId,preset:'Half orbit right',duration:6,resolution:'480P',instructions:'',qualityRetry:true,keyframes:presetKeyframes('Half orbit right')}}
export function cameraPose(frames:CameraKeyframe[],time:number){
 const sorted=[...frames].sort((a,b)=>a.time-b.time);const last=sorted.at(-1)!;
 if(time<=sorted[0].time)return {...sorted[0],time};if(time>=last.time)return {...last,time};
 const index=sorted.findIndex(f=>f.time>=time),a=sorted[index-1],b=sorted[index],p=(time-a.time)/(b.time-a.time||1);
 return {id:a.id,time,orbit:a.orbit+(b.orbit-a.orbit)*p,elevation:a.elevation+(b.elevation-a.elevation)*p,distance:a.distance+(b.distance-a.distance)*p};
}
export function editCameraKeyframe(frames:CameraKeyframe[],id:string,patch:Partial<CameraKeyframe>){
 const index=frames.findIndex(f=>f.id===id);if(index<0)return frames;
 return frames.map((f,i)=>{if(i!==index)return f;const valid=(value:number|undefined,fallback:number,min:number,max:number)=>Number.isFinite(value)?clamp(value!,min,max):fallback;
 return {...f,time:index===0?0:index===frames.length-1?100:valid(patch.time,f.time,frames[index-1].time+1,frames[index+1].time-1),orbit:valid(patch.orbit,f.orbit,-720,720),elevation:valid(patch.elevation,f.elevation,-80,80),distance:valid(patch.distance,f.distance,.3,3)};
 });
}
