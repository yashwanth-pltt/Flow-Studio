'use client';
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { usePlatform, normalizePaletteLanguage } from '@palettelab/sdk';
import { messages } from './messages';

// The UI follows the Palette OS language setting (no in-app switch): Korean
// when the platform is set to Korean, English otherwise.
export type Lang = 'en' | 'ko';
type Vars = Record<string, string | number>;
export type Translate = (text: string, vars?: Vars) => string;

const lookup = new Map<string, readonly [string, string]>();
for (const pair of messages) {
  if (!lookup.has(pair[0])) lookup.set(pair[0], pair);
  if (!lookup.has(pair[1])) lookup.set(pair[1], pair);
}

/** Translate either side of an [English, Korean] pair into `lang`, filling
 * `{name}` placeholders. Unknown strings pass through unchanged. */
export function translate(lang: Lang, text: string, vars?: Vars) {
  const pair = lookup.get(text);
  const out = pair ? pair[lang === 'en' ? 0 : 1] : text;
  return vars ? out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : out;
}

type I18n = { lang: Lang; t: Translate; locale: string };
const I18nContext = createContext<I18n>({ lang: 'en', t: (s, v) => translate('en', s, v), locale: 'en-US' });

export function I18nProvider({ children }: { children: ReactNode }) {
  const platform = usePlatform() as { language?: string | null };
  const lang: Lang = normalizePaletteLanguage(platform?.language, 'en') === 'ko' ? 'ko' : 'en';
  const value = useMemo<I18n>(() => ({
    lang, locale: lang === 'ko' ? 'ko-KR' : 'en-US', t: (text, vars) => translate(lang, text, vars),
  }), [lang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
