// Generation settings. Engine and model values are the backend's real model
// ids; labels, availability and capabilities come from `GET /models`
// (see hooks/use-models.tsx).
export const videoEngines = ['kling_3_0_standard', 'kling_3_0_pro', 'kling_o3_pro', 'kling_4_0', 'seedance_2_0', 'seedance_2_5', 'ltx_video'] as const;
export const videoRatios = ['21:9', '16:9', '4:3', '1:1', '3:4', '9:16'] as const;
// 'auto' = the model takes no resolution; the export then uses the last explicit choice.
export const videoResolutions = ['auto', '480p', '720p', '1080p', '4k'] as const;
export const videoResolutionLabel = (r: string) => r === 'auto' ? 'Auto' : r === '4k' ? '4K' : r;
const shortSide = (r: string) => r === '4k' ? 2160 : parseInt(r) || 720;
export const imageEngines = ['auto', 'nano_banana_pro', 'openai/gpt-image-2.5', 'midjourney', 'flux_schnell'] as const;
// Saved settings that still name a replaced model.
const legacyImageEngines: Record<string, typeof imageEngines[number]> = {'openai/gpt-image-2': 'openai/gpt-image-2.5'};
export const imageRatios = ['Auto', '1:1', '3:2', '2:3', '16:9', '9:16', '4:3', '3:4', '21:9'] as const;
export const imageResolutions = ['Auto', '1K', '2K', '4K'] as const;
export const imageQualities = ['auto', 'low', 'medium', 'high'] as const;
export const textModels = ['palette', 'claude_opus', 'claude_sonnet', 'claude_haiku', 'gpt', 'gemini', 'router'] as const;
export type ImageGenerationSettings = {
  engine: typeof imageEngines[number]; ratio: typeof imageRatios[number];
  resolution: typeof imageResolutions[number]; quality: typeof imageQualities[number]; count: number;
};
export const defaultImageGeneration: ImageGenerationSettings = {engine:'auto',ratio:'16:9',resolution:'1K',quality:'auto',count:1};
const legacyQuality: Record<string, ImageGenerationSettings['quality']> = {'Auto':'auto','낮음':'low','중간':'medium','높음':'high'};
export function imageGenerationSettings(input?: Partial<ImageGenerationSettings>): ImageGenerationSettings {
  const g = {...defaultImageGeneration,...input};
  g.engine = legacyImageEngines[g.engine as string] ?? g.engine;
  const quality = (legacyQuality[g.quality as string] ?? g.quality) as ImageGenerationSettings['quality'];
  return {
    engine:imageEngines.includes(g.engine)?g.engine:defaultImageGeneration.engine,
    ratio:imageRatios.includes(g.ratio)?g.ratio:defaultImageGeneration.ratio,
    resolution:imageResolutions.includes(g.resolution)?g.resolution:defaultImageGeneration.resolution,
    quality:imageQualities.includes(quality)?quality:defaultImageGeneration.quality,
    count:Number.isFinite(g.count)?Math.max(1,Math.min(4,Math.round(g.count))):1,
  };
}
export type GenerationSettings = {
  image: ImageGenerationSettings;
  mode: 'auto' | 'manual';
  /** Text model for planning / chat (see textModels). */
  model: string;
  effort: number;
  engine: typeof videoEngines[number];
  /** Target total length of the video, in seconds. */
  duration: number;
  ratio: typeof videoRatios[number];
  resolution: typeof videoResolutions[number];
  bitrate: 'high' | 'standard';
  /** Flow: chain the scenes so each clip ends on the next scene's opening frame (one continuous shot). */
  flow: boolean;
  /** Sound: generate clips with their own audio (native sound effects / ambience). */
  audio: boolean;
};
export const defaultGeneration: GenerationSettings = {image:{...defaultImageGeneration},mode:'manual',model:'palette',effort:1,engine:'kling_3_0_standard',duration:20,ratio:'16:9',resolution:'720p',bitrate:'high',flow:true,audio:true};
export function generationSettings(input?: Partial<GenerationSettings>): GenerationSettings {
  const g={...defaultGeneration,...input};
  return {...g,image:imageGenerationSettings(input?.image),mode:g.mode==='auto'?'auto':'manual',model:(textModels as readonly string[]).includes(g.model)?g.model:defaultGeneration.model,effort:[0,1,2].includes(g.effort)?g.effort:1,engine:videoEngines.includes(g.engine)?g.engine:defaultGeneration.engine,duration:Number.isFinite(g.duration)?Math.min(120,Math.max(3,Math.round(g.duration))):20,ratio:videoRatios.includes(g.ratio)?g.ratio:'16:9',resolution:videoResolutions.includes(g.resolution)?g.resolution:'720p',bitrate:g.bitrate==='standard'?'standard':'high',flow:g.flow!==false,audio:g.audio!==false};
}
export function videoOutputSize(g: GenerationSettings) {
  const [x,y]=g.ratio.split(':').map(Number),short=shortSide(g.resolution);
  const width=Math.round((x>=y?short*x/y:short)/2)*2;
  const height=Math.round((x>=y?short:short*y/x)/2)*2;
  return {width,height,bitsPerSecond:Math.round((g.bitrate==='high'?8_000_000:3_000_000)*width*height/(1280*720))};
}
