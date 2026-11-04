'use client';
import { createContext, useContext } from 'react';
import type { Catalog } from '@/lib/api';

// Model catalog from GET /models (text / video / image models + availability).
export const emptyCatalog: Catalog = {
  text: [], default_text: 'palette', video: [], default_video: 'kling_3_0_standard', image: [],
  audio: { tts: false, music: false }, ffmpeg: false, providers: {},
};
const ModelsContext = createContext<Catalog>(emptyCatalog);
export const ModelsProvider = ModelsContext.Provider;
export function useModels(): Catalog {
  return useContext(ModelsContext);
}
