'use client';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { usePlatform } from '@palettelab/sdk';

// The UI follows the Palette OS appearance (no in-app switch), like the language.
// Palette OS reports it as `usePlatform().colorMode` and updates it live; hosts
// that predate it fall back to the browser's `prefers-color-scheme`.
export type ColorMode = 'light' | 'dark';

function systemMode(): ColorMode {
  try { return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; } catch { return 'dark'; }
}

const ColorModeContext = createContext<ColorMode>('dark');

export function ColorModeProvider({ children }: { children: ReactNode }) {
  const platform = usePlatform() as { colorMode?: ColorMode | null };
  const [fallback, setFallback] = useState<ColorMode>(() => (typeof window === 'undefined' ? 'dark' : systemMode()));
  useEffect(() => {
    let media: MediaQueryList;
    try { media = window.matchMedia('(prefers-color-scheme: light)'); } catch { return; }
    const sync = () => setFallback(media.matches ? 'light' : 'dark');
    sync();
    media.addEventListener?.('change', sync);
    return () => media.removeEventListener?.('change', sync);
  }, []);
  const mode: ColorMode = platform?.colorMode === 'light' || platform?.colorMode === 'dark' ? platform.colorMode : fallback;
  return <ColorModeContext.Provider value={mode}>{children}</ColorModeContext.Provider>;
}

export function useColorMode() {
  return useContext(ColorModeContext);
}
