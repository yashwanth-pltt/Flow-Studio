'use client';
import { ChevronDown, X, Film, ImageIcon, RectangleHorizontal, Diamond, Type, Check } from 'lucide-react';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { useI18n } from '@/lib/i18n';
import { useModels } from '@/hooks/use-models';
import { imageRatios, imageResolutions, imageQualities, videoRatios, videoResolutions, videoResolutionLabel, type ImageGenerationSettings, type GenerationSettings } from '@/lib/palette/generation';
export type PickerProps={settings:GenerationSettings;onChange:(patch:Partial<GenerationSettings>)=>void;open:string;onOpen:(value:string)=>void;landing?:boolean};
const QUALITY_LABEL:Record<string,string>={auto:'Auto',low:'Low',medium:'Medium',high:'High'};

/** Text model for planning and chat. Availability comes from the model catalog; the UI never names keys or providers. */
export function TextSettings({settings,onChange,open,onOpen,landing}:PickerProps){
 const {t}=useI18n();const catalog=useModels();
 const models=catalog.text.length?catalog.text:[{id:settings.model,label:settings.model,provider:'',available:true}];
 const current=models.find(m=>m.id===settings.model)||models[0];
 return <Popover open={open==='text'} onOpenChange={yes=>onOpen(yes?'text':'')}>
  <PopoverTrigger asChild>
   <button className="video-settings-trigger text-model-trigger" aria-label={t('Text model')} title={`${t('Text model')} · ${current?.label}`}>
    <span className="video-settings-summary">
     <span className="video-settings-line"><span>{t('Text')}</span><b>{current?.label}</b></span>
     {!landing&&<span className="video-settings-line"><b>{current?.available?t('Writes scenes & chat'):t('Not available')}</b></span>}
    </span><ChevronDown size={10}/>
   </button>
  </PopoverTrigger>
  <PopoverContent className="composer-picker text-model-picker" side={landing?'bottom':'top'} align="start" sideOffset={8} aria-label={t('Text model')}>
   <header><span>{t('Text model')}</span><button aria-label={t('Close')} onClick={()=>onOpen('')}><X size={13}/></button></header>
   <div className="text-model-list" role="listbox" aria-label={t('Text model')}>{models.map(m=><button key={m.id} type="button" role="option" aria-selected={m.id===settings.model} disabled={!m.available} title={m.available?m.label:t('Not available')} onClick={()=>{onChange({model:m.id});onOpen('')}}><Type size={13}/><span><strong>{m.label}</strong><small>{t(m.available?'Available':'Not available')}</small></span>{m.id===settings.model&&<Check size={14}/>}</button>)}</div>
  </PopoverContent>
 </Popover>;
}

export function VideoSettings({settings,onChange,open,onOpen,landing}:PickerProps){
 const {t}=useI18n();const catalog=useModels();
 const engines=catalog.video;
 const model=engines.find(m=>m.id===settings.engine);
 const ratios=(model?.aspect_ratios||videoRatios) as string[];
 const resolutions=(model?.resolutions||videoResolutions) as string[];
 const label=model?.label||settings.engine;
 // What this model will actually use; the stored choice is kept for models without a resolution setting.
 const resolution=resolutions.includes(settings.resolution)?settings.resolution:resolutions[0];
 const soundOn=settings.audio||!!model?.audio_fixed;
 const output = `${settings.duration}s · ${settings.ratio} · ${t(videoResolutionLabel(resolution))}${settings.flow?` · ${t('Flow')}`:''}${soundOn?'':` · ${t('Sound off')}`}`;
 const pickEngine=(engine:string)=>{const m=engines.find(x=>x.id===engine);const patch:Partial<GenerationSettings>={engine:engine as GenerationSettings['engine']};if(m&&!m.aspect_ratios.includes(settings.ratio))patch.ratio=m.aspect_ratios.includes('16:9')?'16:9':m.aspect_ratios[0] as GenerationSettings['ratio'];if(m&&!m.resolutions.includes(settings.resolution)&&!m.resolutions.every(r=>r==='auto'))patch.resolution=(m.resolutions.includes('720p')?'720p':m.resolutions[0]) as GenerationSettings['resolution'];onChange(patch)};
 return <Popover open={open==='video'} onOpenChange={yes=>onOpen(yes?'video':'')}>
  <PopoverTrigger asChild>
   <button className="video-settings-trigger" aria-label={t('Video settings')} title={`${t('Video')} ${label} · ${output}`}>
    <span className="video-settings-summary">
     <span className="video-settings-line"><span>{t('Video')}</span><b>{label}</b></span>
     <span className="video-settings-line"><b>{output}</b></span>
    </span>
    <ChevronDown size={10}/>
   </button>
  </PopoverTrigger>
  <PopoverContent className="composer-picker video-output-picker" side={landing?'bottom':'top'} align="start" sideOffset={8} aria-label={t('Video settings options')}>
   <header><span>{t('Video')}</span><button aria-label={t('Close video settings')} onClick={()=>onOpen('')}><X size={13}/></button></header>
   <div className="output-select-grid">
    <label><span>{t('Aspect ratio')}</span><Select value={settings.ratio} onValueChange={ratio=>onChange({ratio:ratio as GenerationSettings['ratio']})}><SelectTrigger aria-label={t('Aspect ratio')}><RectangleHorizontal size={13}/><SelectValue/></SelectTrigger><SelectContent>{ratios.map(r=><SelectItem value={r} key={r}>{r}</SelectItem>)}</SelectContent></Select></label>
    <label><span>{t('Resolution')}</span><Select value={resolution} onValueChange={resolution=>onChange({resolution:resolution as GenerationSettings['resolution']})}><SelectTrigger aria-label={t('Resolution')}><Diamond size={13}/><SelectValue/></SelectTrigger><SelectContent>{resolutions.map(r=><SelectItem key={r} value={r}>{t(videoResolutionLabel(r))}</SelectItem>)}</SelectContent></Select></label>
   </div>
   <div className="output-duration-label"><label>{t('Video length')}</label><output>{settings.duration}s</output></div>
   <Slider aria-label={t('Video length')} min={3} max={120} step={1} value={[settings.duration]} onValueChange={([duration])=>onChange({duration})}/>
   <div className="duration-bounds"><span>3s</span><span>120s</span></div>
   <div className="output-video-model"><span><Film size={13}/>{t('Video model')}</span>
    <Select value={settings.engine} onValueChange={pickEngine}>
     <SelectTrigger aria-label={t('Video model')}><SelectValue/></SelectTrigger>
     <SelectContent>{(engines.length?engines:[{id:settings.engine,label,available:true} as any]).map((m:any)=><SelectItem key={m.id} value={m.id} disabled={!m.available}>{m.label}</SelectItem>)}</SelectContent>
    </Select>
   </div>
   <label className="flow-toggle"><input type="checkbox" checked={soundOn} disabled={!!model?.audio_fixed||model?.audio===false} onChange={e=>onChange({audio:e.target.checked})}/><span><strong>{t('Sound')}</strong><small>{model?.audio===false?t('This model makes silent clips.'):model?.audio_fixed?t('This model always generates sound.'):t(soundOn?'Clips are generated with their own sound.':'Clips are generated silent.')}</small></span></label>
   <label className="flow-toggle"><input type="checkbox" checked={settings.flow} onChange={e=>onChange({flow:e.target.checked})}/><span><strong>{t('Flow · continuous shot')}</strong><small>{model&&!model.end_frame?t('This model can’t end on a set frame, so scenes won’t join seamlessly.'):t('Each clip starts on the previous clip’s last frame, so the video plays as one shot.')}</small></span></label>
  </PopoverContent>
 </Popover>;
}

export function ImageSettings({settings,onChange,open,onOpen,landing}:PickerProps){
 const {t}=useI18n();const catalog=useModels();
 const image=settings.image;
 const engines=catalog.image.length?catalog.image:[{id:image.engine,label:image.engine,available:true}];
 const engineLabel=t(engines.find(m=>m.id===image.engine)?.label||image.engine);
 const output=[t(image.ratio),t(image.resolution),...image.quality==='auto'?[]:[t(QUALITY_LABEL[image.quality])],t('{n} images',{n:image.count})].join(' · ');
 const change=(patch:Partial<ImageGenerationSettings>)=>onChange({image:{...image,...patch}});
 const countControl=<div className="reference-count image-count" role="group" aria-label={t('Image count')}>
   <button type="button" aria-label={t('Fewer images')} disabled={image.count<=1} onClick={()=>change({count:Math.max(1,image.count-1)})}>−</button>
   <output aria-label={t('Number of images')} aria-live="polite">{image.count}</output>
   <button type="button" aria-label={t('More images')} disabled={image.count>=4} onClick={()=>change({count:Math.min(4,image.count+1)})}>+</button>
  </div>;
 return <div className="image-settings-group" role="group" aria-label={t('Image generation settings')}>
  <Popover open={open==='image'} onOpenChange={yes=>onOpen(yes?'image':'')}>
   <PopoverTrigger asChild>
    <button className="video-settings-trigger image-settings-trigger" aria-label={t('Image settings')} title={`${t('Image')} ${engineLabel} · ${output}`}>
     <span className="video-settings-summary">
      <span className="video-settings-line"><span>{t('Image')}</span><b>{engineLabel}</b></span>
      <span className="video-settings-line"><b>{output}</b></span>
     </span><ChevronDown size={10}/>
    </button>
   </PopoverTrigger>
   <PopoverContent className="composer-picker video-output-picker image-output-picker" side={landing?'bottom':'top'} align="start" sideOffset={8} aria-label={t('Image settings options')}>
    <header><span>{t('Image')}</span><button aria-label={t('Close image settings')} onClick={()=>onOpen('')}><X size={13}/></button></header>
    <div className="output-select-grid">
     <label><span>{t('Aspect ratio')}</span><Select value={image.ratio} onValueChange={ratio=>change({ratio:ratio as ImageGenerationSettings['ratio']})}><SelectTrigger aria-label={t('Image ratio')}><RectangleHorizontal size={13}/><SelectValue/></SelectTrigger><SelectContent>{imageRatios.map(r=><SelectItem value={r} key={r}>{t(r)}</SelectItem>)}</SelectContent></Select></label>
     <label><span>{t('Resolution')}</span><Select value={image.resolution} onValueChange={resolution=>change({resolution:resolution as ImageGenerationSettings['resolution']})}><SelectTrigger aria-label={t('Image resolution')}><Diamond size={13}/><SelectValue/></SelectTrigger><SelectContent>{imageResolutions.map(r=><SelectItem key={r} value={r}>{t(r)}</SelectItem>)}</SelectContent></Select></label>
     <label className="image-quality-field"><span>{t('Quality')}</span><Select value={image.quality} onValueChange={quality=>change({quality:quality as ImageGenerationSettings['quality']})}><SelectTrigger aria-label={t('Image quality')}><SelectValue/></SelectTrigger><SelectContent>{imageQualities.map(q=><SelectItem value={q} key={q}>{t(QUALITY_LABEL[q])}</SelectItem>)}</SelectContent></Select></label>
    </div>
    <div className="output-video-model"><span><ImageIcon size={13}/>{t('Image model')}</span><Select value={image.engine} onValueChange={engine=>change({engine:engine as ImageGenerationSettings['engine']})}><SelectTrigger aria-label={t('Image model')}><SelectValue/></SelectTrigger><SelectContent>{engines.map(m=><SelectItem key={m.id} value={m.id} disabled={!m.available}>{t(m.label)}</SelectItem>)}</SelectContent></Select></div>
    <div className="image-count-picker-row"><span>{t('Image count')}</span>{countControl}</div>
   </PopoverContent>
  </Popover>
 </div>;
}
